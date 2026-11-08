import Foundation
import MapperGeometry

struct Ack: Decodable {
    init(from decoder: Decoder) throws {}
}

struct ServerFailure: Error, LocalizedError {
    var message: String
    var status: Int = 0
    var errorDescription: String? { message }
}

struct SessionInfo: Decodable {
    var required: Bool
    var ok: Bool
}

struct PixelRow: Decodable {
    var fixture: String
    var controller: String
    var output: Int
    var index: Int
    var x: Double
    var y: Double
    var z: Double
    var segment: String
    var u: Double

    var mapped: MappedPixel {
        MappedPixel(fixture: fixture, controller: controller, output: output, index: index, x: x, y: y, z: z, segment: segment, u: u)
    }
}

struct PixelMapDocument: Decodable {
    var pixels: [PixelRow]
    var runs: [CountRow]
}

struct CountRow: Decodable {
    var placed: Int
    var expected: Int
    var ok: Bool
}

final class ServerClient {
    var base = URL(string: "http://127.0.0.1:8787")!
    private let session: URLSession
    private let decoder = JSONDecoder()
    private let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }()

    init() {
        let configuration = URLSessionConfiguration.default
        configuration.httpCookieAcceptPolicy = .always
        configuration.timeoutIntervalForRequest = 20
        configuration.waitsForConnectivity = true
        session = URLSession(configuration: configuration)
    }

    func sessionInfo() async throws -> SessionInfo {
        try await send(SessionInfo.self, path: "/api/session")
    }

    func login(_ password: String) async throws {
        struct Body: Encodable { var password: String }
        let _: Ack = try await send(path: "/api/login", method: "POST", body: Body(password: password))
    }

    func controllers() async throws -> [ControllerRecord] {
        struct Box: Decodable { var controllers: [ControllerRecord] }
        return try await send(Box.self, path: "/api/mapper/controllers").controllers
    }

    func fixtures() async throws -> [Fixture] {
        struct Box: Decodable { var fixtures: [Fixture] }
        return try await send(Box.self, path: "/api/mapper/fixtures").fixtures
    }

    func save(_ fixture: Fixture) async throws -> Fixture {
        try await send(Fixture.self, path: "/api/mapper/fixtures", method: "POST", body: fixture)
    }

    func update(_ fixture: Fixture) async throws -> Fixture {
        try await send(Fixture.self, path: "/api/mapper/fixtures/\(fixture.id)", method: "PUT", body: fixture)
    }

    func delete(id: String) async throws {
        let _: Ack = try await send(path: "/api/mapper/fixtures/\(id)", method: "DELETE")
    }

    func pixelmap(id: String) async throws -> [MappedPixel] {
        try await send(PixelMapDocument.self, path: "/api/mapper/fixtures/\(id)/pixelmap").pixels.map(\.mapped)
    }

    func highlight(fixture: String, controller: String, output: Int, index: Int) async throws {
        struct Body: Encodable {
            var controller: String
            var output: Int
            var index: Int
        }
        let _: Ack = try await send(
            path: "/api/mapper/fixtures/\(fixture)/highlight",
            method: "POST",
            body: Body(controller: controller, output: output, index: index)
        )
    }

    func effect(fixture: String, name: String) async throws {
        struct Body: Encodable {
            var name: String
            var params: [String: Double]
        }
        let _: Ack = try await send(
            path: "/api/mapper/fixtures/\(fixture)/effect",
            method: "POST",
            body: Body(name: name, params: ["seconds": 6])
        )
    }

    func scalePitch(fixture: String, run: String) async throws -> Fixture {
        try await send(Fixture.self, path: "/api/mapper/fixtures/\(fixture)/runs/\(run)/scale-pitch", method: "POST", body: [String: String]())
    }

    func stop(fixture: String) async throws {
        let _: Ack = try await send(path: "/api/mapper/fixtures/\(fixture)/stop", method: "POST", body: [String: String]())
    }

    func ledmap(fixture: String, slot: Int, mode: String, width: Int, height: Int) async throws {
        struct Body: Encodable {
            var slot: Int
            var mode: String
            var width: Int
            var height: Int
            var confirm: Bool
        }
        let _: Ack = try await send(
            path: "/api/mapper/fixtures/\(fixture)/ledmap",
            method: "POST",
            body: Body(slot: slot, mode: mode, width: width, height: height, confirm: true)
        )
    }

    func export(fixture: String, format: String, width: Int, height: Int, depth: Int = 1) async throws -> URL {
        let path = "/api/mapper/fixtures/\(fixture)/export?format=\(format)&width=\(width)&height=\(height)&depth=\(depth)"
        let (data, response) = try await session.data(for: request(path))
        try check(response, data)
        let ext: String
        switch format {
        case "csv": ext = "csv"
        case "xmodel", "polyline": ext = "xmodel"
        case "pixelblaze": ext = "json"
        default: ext = "json"
        }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("fixture-\(fixture).\(ext)")
        try data.write(to: url, options: .atomic)
        return url
    }

    func importFixture(name: String, filename: String, text: String) async throws -> Fixture {
        struct Body: Encodable {
            var name: String
            var filename: String
            var text: String
        }
        return try await send(
            Fixture.self,
            path: "/api/mapper/import",
            method: "POST",
            body: Body(name: name, filename: filename, text: text)
        )
    }

    func watch(_ onEvent: @escaping (EffectStatus) -> Void) -> Task<Void, Never> {
        Task {
            while !Task.isCancelled {
                do {
                    let (bytes, response) = try await session.bytes(for: request("/api/mapper/live"))
                    if let http = response as? HTTPURLResponse, http.statusCode >= 400 {
                        try await Task.sleep(nanoseconds: 2_000_000_000)
                        continue
                    }
                    for try await line in bytes.lines {
                        guard line.hasPrefix("data:") else { continue }
                        let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        guard let data = payload.data(using: .utf8), let status = try? decoder.decode(EffectStatus.self, from: data) else { continue }
                        onEvent(status)
                    }
                } catch {
                    if Task.isCancelled { return }
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                }
            }
        }
    }

    private func send<Response: Decodable, Body: Encodable>(_ type: Response.Type = Response.self, path: String, method: String = "GET", body: Body) async throws -> Response {
        var req = request(path)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try encoder.encode(body)
        let (data, response) = try await session.data(for: req)
        try check(response, data)
        return try decoder.decode(Response.self, from: data)
    }

    private func send<Response: Decodable>(_ type: Response.Type = Response.self, path: String, method: String = "GET") async throws -> Response {
        var req = request(path)
        req.httpMethod = method
        let (data, response) = try await session.data(for: req)
        try check(response, data)
        return try decoder.decode(Response.self, from: data)
    }

    private func request(_ path: String) -> URLRequest {
        let url = URL(string: path, relativeTo: base)?.absoluteURL ?? base
        return URLRequest(url: url)
    }

    private func check(_ response: URLResponse, _ data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard http.statusCode >= 400 else { return }
        let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"]
        let message = detail as? String ?? "The server returned \(http.statusCode)."
        throw ServerFailure(message: message, status: http.statusCode)
    }
}

enum FoundServer: Identifiable, Equatable {
    case resolved(name: String, url: String)
    var id: String {
        switch self {
        case .resolved(let name, let url): return name + url
        }
    }
    var title: String {
        switch self {
        case .resolved(let name, _): return name
        }
    }
    var url: String {
        switch self {
        case .resolved(_, let url): return url
        }
    }
}
