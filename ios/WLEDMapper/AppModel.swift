import Foundation
import MapperGeometry
import Network
import Security
import SwiftUI
import UIKit

enum Screenshot: String {
    case connect
    case fixtures
    case preview
}

@MainActor
final class AppModel: ObservableObject {
    static let treeFixtureId = "33333333-3333-4333-8333-333333333333"

    @Published var serverURL = ""
    @Published var password = ""
    @Published var needsPassword = false
    @Published var connected = false
    @Published var busy = false
    @Published var message: String?
    @Published var fixtures: [Fixture] = []
    @Published var controllers: [ControllerRecord] = []
    @Published var servers: [FoundServer] = []
    @Published var status = EffectStatus()
    @Published var path: [Route] = []
    @Published var shareURL: URL?
    @Published var ledmapSlot = 1
    @Published var gridWidth = 16
    @Published var gridHeight = 16

    let client = ServerClient()
    let screenshot: Screenshot?
    private var browser: NWBrowser?
    private var live: Task<Void, Never>?
    private var resolutions: [NWConnection] = []

    init() {
        let args = CommandLine.arguments
        if let index = args.firstIndex(of: "-mapperScreenshot"), index + 1 < args.count {
            screenshot = Screenshot(rawValue: args[index + 1])
        } else {
            screenshot = nil
        }
        if screenshot != nil {
            UIView.setAnimationsEnabled(false)
            serverURL = "http://truenas.local:8787"
            if screenshot != .connect {
                connected = true
                fixtures = Self.bundledFixtures()
            }
        }
    }

    static func bundledFixtures() -> [Fixture] {
        ["straight-1m", "wall-outline", "branching-tree"].compactMap { name in
            guard let url = Bundle.main.url(forResource: name, withExtension: "json"),
                  let data = try? Data(contentsOf: url),
                  let fixture = try? JSONDecoder().decode(Fixture.self, from: data) else { return nil }
            return fixture
        }
    }

    func restore() async {
        if screenshot != nil {
            await loadScreenshots()
            return
        }
        if let saved = UserDefaults.standard.string(forKey: "wledmapper.url"), !saved.isEmpty {
            serverURL = saved
        }
        if let stored = SecretStore.load() {
            password = stored
        }
        if let data = UserDefaults.standard.data(forKey: "wledmapper.fixtures"),
           let cached = try? JSONDecoder().decode([Fixture].self, from: data) {
            fixtures = cached
            if !cached.isEmpty { connected = true }
        }
        startBrowsing()
        if !serverURL.isEmpty {
            await connect(quiet: true)
        }
    }

    func connect(quiet: Bool = false) async {
        let normalized = Self.normalize(serverURL)
        guard let url = URL(string: normalized), url.host != nil else {
            if !quiet { message = "Enter the server address, like http://truenas:8787" }
            return
        }
        serverURL = normalized
        client.base = url
        busy = true
        defer { busy = false }
        do {
            let info = try await client.sessionInfo()
            if info.required && !info.ok {
                needsPassword = true
                guard !password.isEmpty else { return }
                try await client.login(password)
                SecretStore.save(password)
            }
            needsPassword = false
            connected = true
            message = nil
            UserDefaults.standard.set(normalized, forKey: "wledmapper.url")
            try await refresh()
            watch()
        } catch {
            if !quiet || fixtures.isEmpty { message = error.localizedDescription }
            if fixtures.isEmpty { connected = false }
        }
    }

    func refresh() async throws {
        fixtures = try await client.fixtures()
        controllers = try await client.controllers()
        if let data = try? JSONEncoder().encode(fixtures) {
            UserDefaults.standard.set(data, forKey: "wledmapper.fixtures")
        }
    }

    func create(name: String, hint: SurfaceHint) async -> String? {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let fixture = Fixture(
            id: UUID().uuidString,
            name: trimmed.isEmpty ? "New fixture" : trimmed,
            surfaceHint: hint,
            waypoints: [],
            runs: []
        )
        do {
            let saved = try await client.save(fixture)
            fixtures.insert(saved, at: 0)
            Haptics.success()
            return saved.id
        } catch {
            message = error.localizedDescription
            Haptics.warning()
            return nil
        }
    }

    func replace(_ fixture: Fixture) {
        if let index = fixtures.firstIndex(where: { $0.id == fixture.id }) {
            fixtures[index] = fixture
        } else {
            fixtures.insert(fixture, at: 0)
        }
    }

    func delete(_ id: String) async {
        fixtures.removeAll { $0.id == id }
        do {
            try await client.delete(id: id)
        } catch {
            message = error.localizedDescription
        }
    }

    func fixture(_ id: String) -> Fixture? {
        fixtures.first { $0.id == id }
    }

    private func watch() {
        live?.cancel()
        live = client.watch { [weak self] status in
            Task { @MainActor in
                self?.status = status
            }
        }
    }

    private func loadScreenshots() async {
        if fixtures.isEmpty && screenshot != .connect {
            fixtures = Self.bundledFixtures()
        }
    }

    private func startBrowsing() {
        if screenshot != nil { return }
        let browser = NWBrowser(for: .bonjour(type: "_wledmapper._tcp", domain: nil), using: .tcp)
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            Task { @MainActor in
                self?.resolve(results)
            }
        }
        browser.start(queue: .main)
        self.browser = browser
    }

    private func resolve(_ results: Set<NWBrowser.Result>) {
        resolutions.forEach { $0.cancel() }
        resolutions = []
        for result in results {
            guard case .service = result.endpoint else { continue }
            let connection = NWConnection(to: result.endpoint, using: .tcp)
            resolutions.append(connection)
            connection.stateUpdateHandler = { [weak self] state in
                guard case .ready = state else { return }
                guard let endpoint = connection.currentPath?.remoteEndpoint, case .hostPort(let host, let port) = endpoint else { return }
                let url = "http://\(Self.hostText(host)):\(port.rawValue)"
                Task { @MainActor in
                    let item = FoundServer.resolved(name: "WLED Mapper", url: url)
                    if self?.servers.contains(item) == false {
                        self?.servers.append(item)
                    }
                }
                connection.cancel()
            }
            connection.start(queue: .main)
        }
    }

    private static func hostText(_ host: NWEndpoint.Host) -> String {
        switch host {
        case .name(let name, _):
            return name
        case .ipv4(let address):
            return "\(address)"
        case .ipv6(let address):
            return "[\(address)]"
        @unknown default:
            return ""
        }
    }

    static func normalize(_ raw: String) -> String {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return text }
        if !text.contains("://") { text = "http://\(text)" }
        while text.hasSuffix("/") { text.removeLast() }
        return text
    }
}

enum SecretStore {
    private static let service = "wled-mapper"
    private static let account = "fleet-password"

    static func save(_ value: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
        var insert = query
        insert[kSecValueData as String] = Data(value.utf8)
        SecItemAdd(insert as CFDictionary, nil)
    }

    static func load() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
}
