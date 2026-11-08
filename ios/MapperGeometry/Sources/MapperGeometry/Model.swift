import Foundation

public enum SurfaceHint: String, Codable, CaseIterable {
    case planar
    case cylindrical
    case branching
    case freeform
}

public enum WaypointSource: String, Codable {
    case tapped
    case lidarSnapped
    case typed
    case imported
}

public enum SegmentKind: String, Codable, CaseIterable {
    case straight
    case polyline
    case helix
    case curve
    case jump
}

public struct Vec3: Equatable, Hashable {
    public var x: Double
    public var y: Double
    public var z: Double

    public init(_ x: Double, _ y: Double, _ z: Double) {
        self.x = x
        self.y = y
        self.z = z
    }

    public static let zero = Vec3(0, 0, 0)

    public static func + (lhs: Vec3, rhs: Vec3) -> Vec3 {
        Vec3(lhs.x + rhs.x, lhs.y + rhs.y, lhs.z + rhs.z)
    }

    public static func - (lhs: Vec3, rhs: Vec3) -> Vec3 {
        Vec3(lhs.x - rhs.x, lhs.y - rhs.y, lhs.z - rhs.z)
    }

    public static func * (lhs: Vec3, rhs: Double) -> Vec3 {
        Vec3(lhs.x * rhs, lhs.y * rhs, lhs.z * rhs)
    }

    public func dot(_ other: Vec3) -> Double {
        x * other.x + y * other.y + z * other.z
    }

    public func cross(_ other: Vec3) -> Vec3 {
        Vec3(
            y * other.z - z * other.y,
            z * other.x - x * other.z,
            x * other.y - y * other.x
        )
    }

    public func length() -> Double {
        (dot(self)).squareRoot()
    }

    public func normalized() -> Vec3 {
        let magnitude = length()
        if magnitude < 1e-9 { return Vec3(0, 1, 0) }
        return self * (1 / magnitude)
    }

    public var list: [Double] { [x, y, z] }
}

extension Vec3: Codable {
    public init(from decoder: Decoder) throws {
        var container = try decoder.unkeyedContainer()
        let x = try container.decode(Double.self)
        let y = try container.decode(Double.self)
        let z = try container.decode(Double.self)
        self.init(x, y, z)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.unkeyedContainer()
        try container.encode(x)
        try container.encode(y)
        try container.encode(z)
    }
}

public struct Quat: Equatable, Codable {
    public var x: Double
    public var y: Double
    public var z: Double
    public var w: Double

    public init(_ x: Double, _ y: Double, _ z: Double, _ w: Double) {
        self.x = x
        self.y = y
        self.z = z
        self.w = w
    }

    public static let identity = Quat(0, 0, 0, 1)

    public init(from decoder: Decoder) throws {
        var container = try decoder.unkeyedContainer()
        x = try container.decode(Double.self)
        y = try container.decode(Double.self)
        z = try container.decode(Double.self)
        w = try container.decode(Double.self)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.unkeyedContainer()
        try container.encode(x)
        try container.encode(y)
        try container.encode(z)
        try container.encode(w)
    }
}

public struct Origin: Equatable, Codable {
    public var position: Vec3
    public var orientation: Quat

    public init(position: Vec3, orientation: Quat) {
        self.position = position
        self.orientation = orientation
    }

    public static let identity = Origin(position: .zero, orientation: .identity)
}

public struct FrameTransform: Equatable, Codable {
    public var translation: Vec3
    public var yawRadians: Double

    public init(translation: Vec3, yawRadians: Double) {
        self.translation = translation
        self.yawRadians = yawRadians
    }
}

public struct Waypoint: Equatable, Codable, Identifiable {
    public var id: String
    public var position: Vec3
    public var source: WaypointSource
    public var confidence: Double
    public var label: String?

    public init(id: String, position: Vec3, source: WaypointSource, confidence: Double, label: String?) {
        self.id = id
        self.position = position
        self.source = source
        self.confidence = confidence
        self.label = label
    }
}

public struct SegmentParams: Equatable, Codable {
    public var turns: Double?
    public var radiusStartMm: Double?
    public var radiusEndMm: Double?
    public var handedness: String?

    public init(turns: Double? = nil, radiusStartMm: Double? = nil, radiusEndMm: Double? = nil, handedness: String? = nil) {
        self.turns = turns
        self.radiusStartMm = radiusStartMm
        self.radiusEndMm = radiusEndMm
        self.handedness = handedness
    }

    private enum CodingKeys: String, CodingKey {
        case turns, radiusStartMm, radiusEndMm, handedness
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        turns = try container.decodeIfPresent(Double.self, forKey: .turns)
        radiusStartMm = try container.decodeIfPresent(Double.self, forKey: .radiusStartMm)
        radiusEndMm = try container.decodeIfPresent(Double.self, forKey: .radiusEndMm)
        handedness = try container.decodeIfPresent(String.self, forKey: .handedness)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        if let turns { try container.encode(turns, forKey: .turns) }
        if let radiusStartMm { try container.encode(radiusStartMm, forKey: .radiusStartMm) }
        if let radiusEndMm { try container.encode(radiusEndMm, forKey: .radiusEndMm) }
        if let handedness { try container.encode(handedness, forKey: .handedness) }
    }
}

public struct Segment: Equatable, Codable, Identifiable {
    public var id: String
    public var kind: SegmentKind
    public var waypointIds: [String]
    public var params: SegmentParams
    public var pixelCountOverride: Int?

    public init(id: String, kind: SegmentKind, waypointIds: [String], params: SegmentParams, pixelCountOverride: Int? = nil) {
        self.id = id
        self.kind = kind
        self.waypointIds = waypointIds
        self.params = params
        self.pixelCountOverride = pixelCountOverride
    }
}

public struct Run: Equatable, Codable, Identifiable {
    public var id: String
    public var controllerId: String
    public var outputIndex: Int
    public var startIndex: Int
    public var pixelCount: Int
    public var pitchMm: Double
    public var colorOrder: String
    public var segments: [Segment]

    public init(
        id: String,
        controllerId: String,
        outputIndex: Int,
        startIndex: Int,
        pixelCount: Int,
        pitchMm: Double,
        colorOrder: String,
        segments: [Segment]
    ) {
        self.id = id
        self.controllerId = controllerId
        self.outputIndex = outputIndex
        self.startIndex = startIndex
        self.pixelCount = pixelCount
        self.pitchMm = pitchMm
        self.colorOrder = colorOrder
        self.segments = segments
    }
}

public struct Fixture: Equatable, Codable, Identifiable {
    public var schemaVersion: Int
    public var id: String
    public var name: String
    public var origin: Origin
    public var surfaceHint: SurfaceHint
    public var waypoints: [Waypoint]
    public var runs: [Run]
    public var frame: FrameTransform?
    public var createdAt: String?
    public var updatedAt: String?

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, id, name, origin, surfaceHint, waypoints, runs, frame, createdAt, updatedAt
    }

    public init(
        schemaVersion: Int = 1,
        id: String,
        name: String,
        origin: Origin = .identity,
        surfaceHint: SurfaceHint,
        waypoints: [Waypoint],
        runs: [Run],
        frame: FrameTransform? = nil,
        createdAt: String? = nil,
        updatedAt: String? = nil
    ) {
        self.schemaVersion = schemaVersion
        self.id = id
        self.name = name
        self.origin = origin
        self.surfaceHint = surfaceHint
        self.waypoints = waypoints
        self.runs = runs
        self.frame = frame
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        id = try container.decode(String.self, forKey: .id)
        name = try container.decode(String.self, forKey: .name)
        origin = try container.decode(Origin.self, forKey: .origin)
        surfaceHint = try container.decode(SurfaceHint.self, forKey: .surfaceHint)
        waypoints = try container.decode([Waypoint].self, forKey: .waypoints)
        runs = try container.decode([Run].self, forKey: .runs)
        frame = try container.decodeIfPresent(FrameTransform.self, forKey: .frame)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(schemaVersion, forKey: .schemaVersion)
        try container.encode(id, forKey: .id)
        try container.encode(name, forKey: .name)
        try container.encode(origin, forKey: .origin)
        try container.encode(surfaceHint, forKey: .surfaceHint)
        try container.encode(waypoints, forKey: .waypoints)
        try container.encode(runs, forKey: .runs)
        try container.encodeIfPresent(self.frame, forKey: CodingKeys.frame)
        try container.encodeIfPresent(createdAt, forKey: CodingKeys.createdAt)
        try container.encodeIfPresent(updatedAt, forKey: CodingKeys.updatedAt)
    }
}

public struct MappedPixel: Equatable {
    public var fixture: String
    public var controller: String
    public var output: Int
    public var index: Int
    public var x: Double
    public var y: Double
    public var z: Double
    public var segment: String
    public var u: Double

    public init(fixture: String, controller: String, output: Int, index: Int, x: Double, y: Double, z: Double, segment: String, u: Double) {
        self.fixture = fixture
        self.controller = controller
        self.output = output
        self.index = index
        self.x = x
        self.y = y
        self.z = z
        self.segment = segment
        self.u = u
    }

    public var position: Vec3 { Vec3(x, y, z) }
}

public struct RunReport: Equatable {
    public var runId: String
    public var expected: Int
    public var placed: Int
    public var ok: Bool

    public init(runId: String, expected: Int, placed: Int, ok: Bool) {
        self.runId = runId
        self.expected = expected
        self.placed = placed
        self.ok = ok
    }
}

public struct ControllerOutput: Equatable, Codable, Identifiable {
    public var index: Int
    public var start: Int
    public var pixelCount: Int
    public var pin: Int?
    public var colorOrder: String

    public var id: Int { index }

    public init(index: Int, start: Int, pixelCount: Int, pin: Int?, colorOrder: String) {
        self.index = index
        self.start = start
        self.pixelCount = pixelCount
        self.pin = pin
        self.colorOrder = colorOrder
    }
}

public struct ControllerRecord: Equatable, Codable, Identifiable {
    public var id: String
    public var name: String
    public var ip: String
    public var port: Int
    public var online: Bool
    public var arch: String?
    public var version: String?
    public var outputs: [ControllerOutput]

    public init(id: String, name: String, ip: String, port: Int, online: Bool, arch: String?, version: String?, outputs: [ControllerOutput]) {
        self.id = id
        self.name = name
        self.ip = ip
        self.port = port
        self.online = online
        self.arch = arch
        self.version = version
        self.outputs = outputs
    }
}

public struct EffectStatus: Equatable, Codable {
    public var running: Bool
    public var effect: String?
    public var fixtureId: String?
    public var frame: Int
    public var fps: Double
    public var error: String?

    public init(running: Bool = false, effect: String? = nil, fixtureId: String? = nil, frame: Int = 0, fps: Double = 0, error: String? = nil) {
        self.running = running
        self.effect = effect
        self.fixtureId = fixtureId
        self.frame = frame
        self.fps = fps
        self.error = error
    }
}

public enum EffectName: String, CaseIterable {
    case fillBottom = "fill-bottom"
    case heightRainbow = "height-rainbow"
    case angleSpin = "angle-spin"

    public var title: String {
        switch self {
        case .fillBottom: return "Fill from bottom"
        case .heightRainbow: return "Height rainbow"
        case .angleSpin: return "Angle spin"
        }
    }
}
