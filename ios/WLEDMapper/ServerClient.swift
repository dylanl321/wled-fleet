import Foundation
import MapperGeometry

struct Ack: Decodable {
    init(from decoder: Decoder) throws {}
}

struct DetectFrameInfo: Decodable {
    var step: Int
    var role: String
    var bit: Int?
    var lit: [Int]
}

struct DetectSession: Decodable {
    var sessionId: String
    var runId: String
    var controllerId: String
    var outputIndex: Int
    var outputStart: Int
    var mode: String
    var pixelCount: Int
    var indices: [Int]
    var frameCount: Int
    var frames: [DetectFrameInfo]
}

struct DetectStep: Decodable {
    var sessionId: String
    var step: Int
    var role: String
    var bit: Int?
    var lit: [Int]
    var frameCount: Int
    var shown: Bool
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
    var group: String?
    var groupName: String?
    var groupRole: String?
    var branchU: Double?
    var branchHeight: Double?

    var mapped: MappedPixel {
        MappedPixel(
            fixture: fixture,
            controller: controller,
            output: output,
            index: index,
            x: x,
            y: y,
            z: z,
            segment: segment,
            u: u,
            group: group ?? "",
            groupName: groupName ?? "",
            groupRole: groupRole ?? "",
            branchU: branchU ?? 0,
            branchHeight: branchHeight ?? 0
        )
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

    func beginDetect(fixture: String, runId: String, mode: String) async throws -> DetectSession {
        struct Body: Encodable {
            var runId: String
            var mode: String
        }
        return try await send(
            DetectSession.self,
            path: "/api/mapper/fixtures/\(fixture)/detect",
            method: "POST",
            body: Body(runId: runId, mode: mode)
        )
    }

    func stepDetect(fixture: String, session: String, step: Int) async throws -> DetectStep {
        struct Body: Encodable { var step: Int }
        return try await send(
            DetectStep.self,
            path: "/api/mapper/fixtures/\(fixture)/detect/\(session)",
            method: "POST",
            body: Body(step: step)
        )
    }

    func finishDetect(fixture: String, session: String) async throws {
        let _: Ack = try await send(
            path: "/api/mapper/fixtures/\(fixture)/detect/\(session)/finish",
            method: "POST",
            body: [String: String]()
        )
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

    func effect(fixture: String, name: String, address: EffectAddress = EffectAddress()) async throws {
        struct Body: Encodable {
            var name: String
            var params: Params

            struct Params: Encodable {
                var seconds: Double
                var target: String
                var heightM: Double?
                var branchHeightM: Double?
                var branchU: Double?
                var bandM: Double?
                var bandU: Double?

                func encode(to encoder: Encoder) throws {
                    var container = encoder.container(keyedBy: CodingKeys.self)
                    try container.encode(seconds, forKey: .seconds)
                    try container.encode(target, forKey: .target)
                    try container.encodeIfPresent(heightM, forKey: .heightM)
                    try container.encodeIfPresent(branchHeightM, forKey: .branchHeightM)
                    try container.encodeIfPresent(branchU, forKey: .branchU)
                    try container.encodeIfPresent(bandM, forKey: .bandM)
                    try container.encodeIfPresent(bandU, forKey: .bandU)
                }

                private enum CodingKeys: String, CodingKey {
                    case seconds, target, heightM, branchHeightM, branchU, bandM, bandU
                }
            }
        }
        let params = Body.Params(
            seconds: 6,
            target: address.target,
            heightM: address.heightM,
            branchHeightM: address.branchHeightM,
            branchU: address.branchU,
            bandM: address.bandM,
            bandU: address.bandU
        )
        let _: Ack = try await send(
            path: "/api/mapper/fixtures/\(fixture)/effect",
            method: "POST",
            body: Body(name: name, params: params)
        )
    }

    func detectionPlan(fixture: String, mode: String, luma: Double?) async throws -> DetectionPlan {
        struct Body: Encodable {
            var mode: String
            var luma: Double?
        }
        return try await send(DetectionPlan.self, path: "/api/mapper/fixtures/\(fixture)/detect/plan", method: "POST", body: Body(mode: mode, luma: luma))
    }

    func showDetection(fixture: String, pixels: [Int]) async throws {
        struct Body: Encodable { var pixels: [Int] }
        let _: Ack = try await send(path: "/api/mapper/fixtures/\(fixture)/detect/frame", method: "POST", body: Body(pixels: pixels))
    }

    func acceptDetection(fixture: String, corrections: [DetectionCorrection], seen: [Int]) async throws -> (Fixture, [Int]) {
        struct Body: Encodable {
            var corrections: [DetectionCorrection]
            var seen: [Int]
        }
        let data = try await raw(path: "/api/mapper/fixtures/\(fixture)/detect/accept", method: "POST", body: Body(corrections: corrections, seen: seen))
        let fixture = try decoder.decode(Fixture.self, from: data)
        let note = try decoder.decode(DetectionNote.self, from: data)
        return (fixture, note.detection.missing)
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
        try decoder.decode(Response.self, from: try await raw(path: path, method: method, body: body))
    }

    private func raw<Body: Encodable>(path: String, method: String, body: Body) async throws -> Data {
        var req = request(path)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try encoder.encode(body)
        let (data, response) = try await session.data(for: req)
        try check(response, data)
        return data
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

struct DetectionPlan: Decodable {
    var pixelCount: Int
    var frames: [[Int]]
}

struct DetectionCorrection: Encodable {
    var ordinal: Int
    var position: [Double]
}

struct DetectionNote: Decodable {
    struct Info: Decodable {
        var missing: [Int]
        var needsViewpoint: Bool
    }
    var detection: Info
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
