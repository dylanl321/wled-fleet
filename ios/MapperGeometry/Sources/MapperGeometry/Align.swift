import Foundation

public func driftFraction(_ index: Int, count: Int) -> Double {
    guard count > 1 else { return 0 }
    return Double(index) / Double(count - 1)
}

public func distributeDrift(_ points: [Vec3], error: Vec3) -> [Vec3] {
    guard points.count >= 2 else { return points }
    let last = Double(points.count - 1)
    return points.enumerated().map { index, point in
        point - error * (Double(index) / last)
    }
}

public func alignReferences(sourceA: Vec3, sourceB: Vec3, destA: Vec3, destB: Vec3, points: [Vec3]) -> (points: [Vec3], yaw: Double) {
    let source = sourceB - sourceA
    let dest = destB - destA
    let yaw = atan2(dest.x, dest.z) - atan2(source.x, source.z)
    let spun = points.map { applyYaw($0, around: sourceA, yaw: yaw) }
    let shift = destA - applyYaw(sourceA, around: sourceA, yaw: yaw)
    return (spun.map { $0 + shift }, yaw)
}

public func applyYaw(_ point: Vec3, around origin: Vec3, yaw: Double) -> Vec3 {
    let relative = point - origin
    let cosine = cos(yaw)
    let sine = sin(yaw)
    let rotated = Vec3(
        relative.x * cosine + relative.z * sine,
        relative.y,
        -relative.x * sine + relative.z * cosine
    )
    return rotated + origin
}
