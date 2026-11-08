import Foundation

private let eps = 1e-9

public func lerp(_ a: Vec3, _ b: Vec3, _ t: Double) -> Vec3 {
    a + (b - a) * t
}

public func distance(_ a: Vec3, _ b: Vec3) -> Double {
    (b - a).length()
}

public func polylineLength(_ points: [Vec3]) -> Double {
    guard points.count >= 2 else { return 0 }
    var total = 0.0
    for index in 1..<points.count {
        total += distance(points[index - 1], points[index])
    }
    return total
}

public func positionOnPolyline(_ points: [Vec3], _ fraction: Double) -> Vec3 {
    guard let first = points.first else { return .zero }
    if points.count == 1 || fraction <= 0 { return first }
    if fraction >= 1 { return points[points.count - 1] }
    let total = polylineLength(points)
    if total < eps { return first }
    let target = fraction * total
    var walked = 0.0
    for index in 1..<points.count {
        let span = distance(points[index - 1], points[index])
        if walked + span >= target - eps {
            let local = span < eps ? 0 : (target - walked) / span
            return lerp(points[index - 1], points[index], min(max(local, 0), 1))
        }
        walked += span
    }
    return points[points.count - 1]
}

public func helixLength(turns: Double, radius: Double, rise: Double) -> Double {
    if turns <= eps { return abs(rise) }
    let risePerTurn = rise / turns
    return turns * ((2 * Double.pi * radius) * (2 * Double.pi * radius) + risePerTurn * risePerTurn).squareRoot()
}

private func reject(_ vector: Vec3, _ axisUnit: Vec3) -> Vec3 {
    vector - axisUnit * vector.dot(axisUnit)
}

public func helixBasis(_ axisStart: Vec3, _ axisEnd: Vec3, hint: Vec3?) -> (Vec3, Vec3, Vec3) {
    let axis = (axisEnd - axisStart).normalized()
    var e1 = reject(Vec3(1, 0, 0), axis)
    if e1.length() < 1e-8 {
        e1 = reject(Vec3(0, 0, 1), axis)
    }
    e1 = e1.normalized()
    if let hint {
        let hinted = reject(hint, axis)
        if hinted.length() > 1e-6 {
            e1 = hinted.normalized()
        }
    }
    let e2 = axis.cross(e1).normalized()
    return (axis, e1, e2)
}

public func helixPosition(
    axisStart: Vec3,
    axisEnd: Vec3,
    radiusStart: Double,
    radiusEnd: Double,
    turns: Double,
    fraction: Double,
    handedness: String = "ccw",
    hint: Vec3? = nil
) -> Vec3 {
    let u = min(max(fraction, 0), 1)
    let basis = helixBasis(axisStart, axisEnd, hint: hint)
    let radius = radiusStart + (radiusEnd - radiusStart) * u
    let angle = 2 * Double.pi * turns * u
    var sine = sin(angle)
    if handedness == "cw" { sine = -sine }
    let radial = basis.1 * (radius * cos(angle)) + basis.2 * (radius * sine)
    return axisStart + (axisEnd - axisStart) * u + radial
}

public func helixArcLength(
    axisStart: Vec3,
    axisEnd: Vec3,
    radiusStart: Double,
    radiusEnd: Double,
    turns: Double,
    handedness: String = "ccw",
    hint: Vec3? = nil
) -> Double {
    if abs(radiusEnd - radiusStart) < 1e-9 {
        return helixLength(turns: turns, radius: radiusStart, rise: (axisEnd - axisStart).length())
    }
    let steps = 256
    var total = 0.0
    var previous = helixPosition(
        axisStart: axisStart, axisEnd: axisEnd, radiusStart: radiusStart, radiusEnd: radiusEnd,
        turns: turns, fraction: 0, handedness: handedness, hint: hint
    )
    for step in 1...steps {
        let point = helixPosition(
            axisStart: axisStart, axisEnd: axisEnd, radiusStart: radiusStart, radiusEnd: radiusEnd,
            turns: turns, fraction: Double(step) / Double(steps), handedness: handedness, hint: hint
        )
        total += distance(previous, point)
        previous = point
    }
    return total
}

public func catmullRom(_ p0: Vec3, _ p1: Vec3, _ p2: Vec3, _ p3: Vec3, _ t: Double) -> Vec3 {
    let t2 = t * t
    let t3 = t2 * t
    return (
        p1 * 2
            + (p2 - p0) * t
            + (p0 * 2 - p1 * 5 + p2 * 4 - p3) * t2
            + (p1 * 3 - p0 - p2 * 3 + p3) * t3
    ) * 0.5
}

public func curveSamples(_ points: [Vec3], perSpan: Int = 40) -> [Vec3] {
    if points.count < 2 { return points }
    if points.count == 2 { return [points[0], points[1]] }
    var extended = [points[0]]
    extended.append(contentsOf: points)
    extended.append(points[points.count - 1])
    var samples = [points[0]]
    let spans = points.count - 1
    for index in 0..<spans {
        let p0 = extended[index]
        let p1 = extended[index + 1]
        let p2 = extended[index + 2]
        let p3 = extended[index + 3]
        if perSpan > 0 {
            for step in 1...perSpan {
                samples.append(catmullRom(p0, p1, p2, p3, Double(step) / Double(perSpan)))
            }
        }
    }
    return samples
}

public func curveLength(_ points: [Vec3]) -> Double {
    polylineLength(curveSamples(points))
}

public func curvePosition(_ points: [Vec3], _ fraction: Double) -> Vec3 {
    positionOnPolyline(curveSamples(points), fraction)
}

public func centroid(_ points: [Vec3]) -> Vec3 {
    guard !points.isEmpty else { return .zero }
    let scale = 1 / Double(points.count)
    return points.reduce(.zero, +) * scale
}

public struct HelixFit: Equatable {
    public var axisStart: Vec3
    public var axisEnd: Vec3
    public var radiusStart: Double
    public var radiusEnd: Double
    public var turns: Double
    public var radii: [Double]
}

public func fitHelix(_ taps: [Vec3]) throws -> HelixFit {
    if taps.count < 2 { throw GeometryError("A helix fit needs at least two taps.") }
    let axis = helixAxis(taps)
    let start = projectToLine(taps[0], axis.center, axis.direction)
    let end = projectToLine(taps[taps.count - 1], axis.center, axis.direction)
    let radii = taps.map { distance($0, projectToLine($0, axis.center, axis.direction)) }
    return HelixFit(
        axisStart: start,
        axisEnd: end,
        radiusStart: radii[0],
        radiusEnd: radii[radii.count - 1],
        turns: Double(taps.count - 1),
        radii: radii
    )
}

private func helixAxis(_ taps: [Vec3]) -> (center: Vec3, direction: Vec3) {
    let vertical = taps.map(\.y).max()! - taps.map(\.y).min()!
    let horizontal = max(
        taps.map(\.x).max()! - taps.map(\.x).min()!,
        taps.map(\.z).max()! - taps.map(\.z).min()!
    )
    let center = centroid(taps)
    if vertical >= max(horizontal * 2, 1e-6) {
        let direction = Vec3(0, taps[taps.count - 1].y >= taps[0].y ? 1 : -1, 0)
        return (center, direction)
    }
    return fitLine(taps)
}

private func fitLine(_ points: [Vec3]) -> (center: Vec3, direction: Vec3) {
    let center = centroid(points)
    var direction = Vec3(0, 1, 0)
    for _ in 0..<48 {
        var updated = Vec3.zero
        for point in points {
            let delta = point - center
            updated = updated + delta * delta.dot(direction)
        }
        if updated.length() < 1e-12 { break }
        direction = updated.normalized()
    }
    if points.count >= 2 && (points[points.count - 1] - points[0]).dot(direction) < 0 {
        direction = direction * -1
    }
    return (center, direction)
}

private func projectToLine(_ point: Vec3, _ origin: Vec3, _ direction: Vec3) -> Vec3 {
    origin + direction * (point - origin).dot(direction)
}

public func fitCircle(_ points: [(Double, Double)]) throws -> (x: Double, z: Double, radius: Double) {
    if points.count < 3 { throw GeometryError("A circle fit needs at least three points.") }
    var sumX = 0.0, sumZ = 0.0, sumXX = 0.0, sumZZ = 0.0, sumXZ = 0.0
    var sumR = 0.0, sumRX = 0.0, sumRZ = 0.0
    for (x, z) in points {
        let r2 = x * x + z * z
        sumX += x
        sumZ += z
        sumXX += x * x
        sumZZ += z * z
        sumXZ += x * z
        sumR += r2
        sumRX += r2 * x
        sumRZ += r2 * z
    }
    let count = Double(points.count)
    let matrix = [
        [sumXX, sumXZ, sumX],
        [sumXZ, sumZZ, sumZ],
        [sumX, sumZ, count],
    ]
    guard let solution = solve3(matrix, [sumRX, sumRZ, sumR]) else {
        throw GeometryError("Those points do not sit on a circle.")
    }
    let centerX = solution[0] / 2
    let centerZ = solution[1] / 2
    let radiusSq = solution[2] + centerX * centerX + centerZ * centerZ
    if radiusSq <= 0 { throw GeometryError("Those points do not sit on a circle.") }
    return (centerX, centerZ, radiusSq.squareRoot())
}

private func solve3(_ matrix: [[Double]], _ vector: [Double]) -> [Double]? {
    var rows = (0..<3).map { matrix[$0] + [vector[$0]] }
    for column in 0..<3 {
        var pivot = column
        for row in (column + 1)..<3 where abs(rows[row][column]) > abs(rows[pivot][column]) {
            pivot = row
        }
        if abs(rows[pivot][column]) < 1e-12 { return nil }
        rows.swapAt(column, pivot)
        let scale = rows[column][column]
        rows[column] = rows[column].map { $0 / scale }
        for row in 0..<3 where row != column {
            let factor = rows[row][column]
            rows[row] = zip(rows[row], rows[column]).map { $0 - factor * $1 }
        }
    }
    return [rows[0][3], rows[1][3], rows[2][3]]
}

public func nudgeTowardCamera(hit: Vec3, camera: Vec3, meters: Double) -> Vec3 {
    let ray = hit - camera
    if ray.length() < eps { return hit }
    return hit - ray.normalized() * meters
}

public func applyFrame(_ point: Vec3, _ frame: FrameTransform) -> Vec3 {
    let shifted = point + frame.translation
    let cosine = cos(frame.yawRadians)
    let sine = sin(frame.yawRadians)
    return Vec3(
        shifted.x * cosine + shifted.z * sine,
        shifted.y,
        -shifted.x * sine + shifted.z * cosine
    )
}

public func normalisePoints(_ points: [Vec3]) -> (points: [Vec3], frame: FrameTransform) {
    guard !points.isEmpty else { return ([], FrameTransform(translation: .zero, yawRadians: 0)) }
    let minY = points.map(\.y).min()!
    let shifted = points.map { Vec3($0.x, $0.y - minY, $0.z) }
    let center = centroid(shifted)
    let translation = Vec3(-center.x, -minY, -center.z)
    let recentered = shifted.map { Vec3($0.x - center.x, $0.y, $0.z - center.z) }
    let first = recentered[0]
    var yaw = 0.0
    if hypot(first.x, first.z) > 1e-6 {
        yaw = -atan2(first.x, first.z)
    }
    let frame = FrameTransform(translation: translation, yawRadians: yaw)
    return (points.map { applyFrame($0, frame) }, frame)
}

public struct GeometryError: Error, CustomStringConvertible {
    public var description: String
    public init(_ description: String) { self.description = description }
}
