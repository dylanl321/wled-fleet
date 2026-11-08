import Foundation

public struct ImageBlob: Equatable {
    public var u: Double
    public var v: Double

    public init(_ u: Double, _ v: Double) {
        self.u = u
        self.v = v
    }
}

public struct DecodedSighting: Equatable {
    public var index: Int
    public var u: Double
    public var v: Double

    public init(index: Int, u: Double, v: Double) {
        self.index = index
        self.u = u
        self.v = v
    }
}

public func frameCount(_ pixelCount: Int) -> Int {
    if pixelCount <= 1 { return 1 }
    return Int(ceil(log2(Double(pixelCount))))
}

public func lightsForFrame(_ pixelCount: Int, _ frame: Int) -> [Int] {
    (0..<pixelCount).filter { ($0 >> frame) & 1 == 1 }
}

public func detectionFrames(_ pixelCount: Int, mode: String) -> [[Int]] {
    if mode == "single" { return (0..<pixelCount).map { [$0] } }
    return (0..<frameCount(pixelCount)).map { lightsForFrame(pixelCount, $0) }
}

public func decodeBits(_ seen: [Bool]) -> Int {
    var value = 0
    for (bit, on) in seen.enumerated() where on {
        value |= 1 << bit
    }
    return value
}

public func isDark(_ averageLuma: Double, limit: Double = 40) -> Bool {
    averageLuma <= limit
}

public func missingIndices(_ pixelCount: Int, seen: Set<Int>) -> [Int] {
    (0..<pixelCount).filter { !seen.contains($0) }
}

public func decodeStationary(_ frames: [[ImageBlob]], join: Double = 8) -> [DecodedSighting] {
    struct Cluster {
        var u: Double
        var v: Double
        var count: Double
        var bits: [Bool]
    }
    var clusters: [Cluster] = []
    for (frameIndex, blobs) in frames.enumerated() {
        for blob in blobs {
            var best = -1
            var bestDistance = join
            for (index, cluster) in clusters.enumerated() {
                let gap = hypot(cluster.u - blob.u, cluster.v - blob.v)
                if gap <= bestDistance {
                    best = index
                    bestDistance = gap
                }
            }
            if best < 0 {
                clusters.append(Cluster(u: blob.u, v: blob.v, count: 0, bits: Array(repeating: false, count: frames.count)))
                best = clusters.count - 1
            }
            let count = clusters[best].count
            clusters[best].u = (clusters[best].u * count + blob.u) / (count + 1)
            clusters[best].v = (clusters[best].v * count + blob.v) / (count + 1)
            clusters[best].count = count + 1
            clusters[best].bits[frameIndex] = true
        }
    }
    return clusters.map { DecodedSighting(index: decodeBits($0.bits), u: $0.u, v: $0.v) }
}

public func brightestBlobs(_ luma: [Double], width: Int, height: Int, threshold: Double, limit: Int, radius: Int = 2) -> [ImageBlob] {
    guard width > 0, height > 0, luma.count >= width * height else { return [] }
    var values = Array(luma.prefix(width * height))
    var found: [ImageBlob] = []
    for _ in 0..<limit {
        guard let index = values.indices.max(by: { values[$0] < values[$1] }), values[index] >= threshold else { break }
        let column = index % width
        let row = index / width
        found.append(ImageBlob(Double(column), Double(row)))
        let yStart = max(0, row - radius)
        let yEnd = min(height, row + radius + 1)
        let xStart = max(0, column - radius)
        let xEnd = min(width, column + radius + 1)
        if yStart < yEnd {
            for y in yStart..<yEnd {
                for x in xStart..<xEnd {
                    values[y * width + x] = 0
                }
            }
        }
    }
    return found
}

public func rayFromPixel(u: Double, v: Double, fx: Double, fy: Double, cx: Double, cy: Double, pose: [[Double]]) -> (origin: Vec3, direction: Vec3) {
    let local = Vec3((u - cx) / fx, (v - cy) / fy, -1).normalized()
    let origin = Vec3(pose[0][3], pose[1][3], pose[2][3])
    let direction = Vec3(
        pose[0][0] * local.x + pose[0][1] * local.y + pose[0][2] * local.z,
        pose[1][0] * local.x + pose[1][1] * local.y + pose[1][2] * local.z,
        pose[2][0] * local.x + pose[2][1] * local.y + pose[2][2] * local.z
    ).normalized()
    return (origin, direction)
}

public func refineWithModel(origin: Vec3, direction: Vec3, segments: [(Vec3, Vec3)], limit: Double = 0.05) -> Vec3? {
    var best: (Double, Vec3)?
    for (start, end) in segments {
        guard let hit = intersectRaySegment(origin, direction, start, end), hit.0 <= limit else { continue }
        if best == nil || hit.0 < best!.0 { best = hit }
    }
    return best?.1
}

public func triangulate(originA: Vec3, directionA: Vec3, originB: Vec3, directionB: Vec3) -> Vec3 {
    let w0 = originA - originB
    let a = directionA.dot(directionA)
    let b = directionA.dot(directionB)
    let c = directionB.dot(directionB)
    let d = directionA.dot(w0)
    let e = directionB.dot(w0)
    let denom = a * c - b * b
    if abs(denom) < 1e-9 { return originA }
    let t = (b * e - c * d) / denom
    let s = (a * e - b * d) / denom
    let pointA = originA + directionA * t
    let pointB = originB + directionB * s
    return (pointA + pointB) * 0.5
}

private func intersectRaySegment(_ origin: Vec3, _ direction: Vec3, _ start: Vec3, _ end: Vec3) -> (Double, Vec3)? {
    let segment = end - start
    let a = direction.dot(direction)
    let b = direction.dot(segment)
    let c = segment.dot(segment)
    let d = direction.dot(origin - start)
    let e = segment.dot(origin - start)
    let denom = a * c - b * b
    if abs(denom) < 1e-9 || c < 1e-9 { return nil }
    let t = (b * e - c * d) / denom
    var s = (a * e - b * d) / denom
    if t < 0 { return nil }
    s = min(max(s, 0), 1)
    let point = start + segment * s
    let closest = origin + direction * t
    return ((closest - point).length(), point)
}
