import Foundation

public enum DetectMode: String, Equatable {
    case binary
    case sequential
}

public enum CodeRole: String, Equatable {
    case off
    case on
    case bit
    case single
}

public struct CodeFrame: Equatable {
    public var step: Int
    public var role: CodeRole
    public var bit: Int?
    public var lit: [Int]

    public init(step: Int, role: CodeRole, bit: Int?, lit: [Int]) {
        self.step = step
        self.role = role
        self.bit = bit
        self.lit = lit
    }
}

public func bitWidth(_ count: Int) -> Int {
    if count <= 1 { return 0 }
    var bits = 0
    var power = 1
    while power < count {
        power *= 2
        bits += 1
    }
    return bits
}

public func codeFrames(indices: [Int], mode: DetectMode) -> [CodeFrame] {
    var frames = [
        CodeFrame(step: 0, role: .off, bit: nil, lit: []),
        CodeFrame(step: 1, role: .on, bit: nil, lit: indices),
    ]
    switch mode {
    case .sequential:
        for index in indices {
            frames.append(CodeFrame(step: frames.count, role: .single, bit: nil, lit: [index]))
        }
    case .binary:
        for bit in 0..<bitWidth(indices.count) {
            let lit = indices.enumerated().compactMap { ordinal, index in
                (ordinal >> bit) & 1 == 1 ? index : nil
            }
            frames.append(CodeFrame(step: frames.count, role: .bit, bit: bit, lit: lit))
        }
    }
    return frames
}

public struct ImagePoint: Equatable {
    public var x: Double
    public var y: Double

    public init(x: Double, y: Double) {
        self.x = x
        self.y = y
    }
}

public struct BrightBlob: Equatable {
    public var centroid: ImagePoint
    public var area: Int
    public var peak: Int

    public init(centroid: ImagePoint, area: Int, peak: Int) {
        self.centroid = centroid
        self.area = area
        self.peak = peak
    }
}

public func detectBrightBlobs(
    luminance: [UInt8],
    width: Int,
    height: Int,
    threshold: Int,
    minimumArea: Int = 2,
    maximumArea: Int = 8000
) -> [BrightBlob] {
    guard width > 0, height > 0, luminance.count >= width * height else { return [] }
    let total = width * height
    var visited = [Bool](repeating: false, count: total)
    var blobs: [BrightBlob] = []
    for start in 0..<total where !visited[start] {
        if Int(luminance[start]) < threshold {
            visited[start] = true
            continue
        }
        var stack = [start]
        visited[start] = true
        var area = 0
        var peak = 0
        var sumW = 0
        var sumX = 0
        var sumY = 0
        while let pixel = stack.popLast() {
            let value = Int(luminance[pixel])
            let x = pixel % width
            let y = pixel / width
            area += 1
            peak = max(peak, value)
            sumW += value
            sumX += x * value
            sumY += y * value
            pushBlob(&stack, &visited, luminance, x - 1, y, width, height, threshold)
            pushBlob(&stack, &visited, luminance, x + 1, y, width, height, threshold)
            pushBlob(&stack, &visited, luminance, x, y - 1, width, height, threshold)
            pushBlob(&stack, &visited, luminance, x, y + 1, width, height, threshold)
        }
        guard area >= minimumArea, area <= maximumArea, sumW > 0 else { continue }
        blobs.append(BrightBlob(centroid: ImagePoint(x: Double(sumX) / Double(sumW), y: Double(sumY) / Double(sumW)), area: area, peak: peak))
    }
    blobs.sort { lhs, rhs in
        if lhs.peak != rhs.peak { return lhs.peak > rhs.peak }
        return lhs.area > rhs.area
    }
    return blobs
}

private func pushBlob(
    _ stack: inout [Int],
    _ visited: inout [Bool],
    _ luminance: [UInt8],
    _ x: Int,
    _ y: Int,
    _ width: Int,
    _ height: Int,
    _ threshold: Int
) {
    guard x >= 0, y >= 0, x < width, y < height else { return }
    let index = y * width + x
    if visited[index] { return }
    if Int(luminance[index]) < threshold {
        visited[index] = true
        return
    }
    visited[index] = true
    stack.append(index)
}

public func downsampleFactor(width: Int, height: Int, longest: Int = 960) -> Int {
    let big = max(width, height)
    if big <= longest || longest < 1 { return 1 }
    return max(1, Int((Double(big) / Double(longest)).rounded(.up)))
}

public struct DownsampledLuma {
    public var bytes: [UInt8]
    public var width: Int
    public var height: Int
}

public func downsampleMax(_ luminance: [UInt8], width: Int, height: Int, factor: Int) -> DownsampledLuma {
    if factor <= 1 {
        return DownsampledLuma(bytes: Array(luminance.prefix(width * height)), width: width, height: height)
    }
    let outW = max(1, width / factor)
    let outH = max(1, height / factor)
    var output = [UInt8](repeating: 0, count: outW * outH)
    for y in 0..<outH {
        for x in 0..<outW {
            var peak = 0
            for dy in 0..<factor {
                let sy = y * factor + dy
                if sy >= height { break }
                for dx in 0..<factor {
                    let sx = x * factor + dx
                    if sx >= width { break }
                    peak = max(peak, Int(luminance[sy * width + sx]))
                }
            }
            output[y * outW + x] = UInt8(peak)
        }
    }
    return DownsampledLuma(bytes: output, width: outW, height: outH)
}

public func fullResolution(_ point: ImagePoint, scale: Int) -> ImagePoint {
    if scale <= 1 { return point }
    let shift = Double(scale - 1) / 2
    return ImagePoint(x: point.x * Double(scale) + shift, y: point.y * Double(scale) + shift)
}

public struct RoomLight: Equatable {
    public var mean: Double
    public var percentile90: Double
    public var dark: Bool
}

public func roomBrightness(_ luminance: [UInt8]) -> RoomLight {
    guard !luminance.isEmpty else { return RoomLight(mean: 255, percentile90: 255, dark: false) }
    var sum = 0
    var histogram = [Int](repeating: 0, count: 256)
    for value in luminance {
        let level = Int(value)
        sum += level
        histogram[level] += 1
    }
    let mean = Double(sum) / Double(luminance.count)
    let target = Int((Double(luminance.count) * 0.90).rounded(.up))
    var seen = 0
    var percentile = 255
    for level in 0..<256 {
        seen += histogram[level]
        if seen >= target {
            percentile = level
            break
        }
    }
    return RoomLight(mean: mean, percentile90: Double(percentile), dark: mean < 36 && percentile < 80)
}

public func thresholdFromReference(off: [UInt8], on: [UInt8]) -> Int {
    let offPeak = off.max().map(Int.init) ?? 0
    let onPeak = on.max().map(Int.init) ?? 0
    if onPeak <= offPeak + 8 { return min(250, offPeak + 12) }
    let mid = (offPeak + onPeak) / 2
    return min(250, max(offPeak + 12, mid))
}

public struct DecodedBlob: Equatable {
    public var index: Int?
    public var centroid: ImagePoint
    public var stable: Bool
}

public func decodeBlobs(frames: [CodeFrame], observations: [[ImagePoint]], matchRadius: Double) -> [DecodedBlob] {
    guard observations.count == frames.count else { return [] }
    var byStep: [Int: [ImagePoint]] = [:]
    for (position, frame) in frames.enumerated() {
        byStep[frame.step] = observations[position]
    }
    let off = byStep[0] ?? []
    let on = byStep[1] ?? []
    let candidates = on.filter { nearest($0, off, matchRadius) == nil }
    let bitFrames = frames.filter { $0.role == .bit }.sorted { ($0.bit ?? 0) < ($1.bit ?? 0) }
    let indices = frames.first { $0.role == .on }?.lit ?? []
    var decoded: [DecodedBlob] = []
    if !bitFrames.isEmpty {
        for point in candidates {
            var ordinal = 0
            for frame in bitFrames where nearest(point, byStep[frame.step] ?? [], matchRadius) != nil {
                ordinal |= 1 << (frame.bit ?? 0)
            }
            let index = (ordinal >= 0 && ordinal < indices.count) ? indices[ordinal] : nil
            decoded.append(DecodedBlob(index: index, centroid: point, stable: true))
        }
    } else if !frames.contains(where: { $0.role == .single }) && indices.count == 1, let only = indices.first {
        for point in candidates {
            decoded.append(DecodedBlob(index: only, centroid: point, stable: true))
        }
    } else {
        var claimed = Set<Int>()
        for frame in frames where frame.role == .single {
            let fresh = (byStep[frame.step] ?? []).filter { nearest($0, off, matchRadius) == nil }
            guard let choiceSeed = fresh.first else { continue }
            var choice = choiceSeed
            let pool = candidates.enumerated().filter { !claimed.contains($0.offset) }
            if let best = pool.min(by: { hypot($0.element.x - choice.x, $0.element.y - choice.y) < hypot($1.element.x - choice.x, $1.element.y - choice.y) }),
               hypot(best.element.x - choice.x, best.element.y - choice.y) <= matchRadius {
                claimed.insert(best.offset)
                choice = best.element
            }
            decoded.append(DecodedBlob(index: frame.lit.first, centroid: choice, stable: true))
        }
    }
    var counts: [Int: Int] = [:]
    for blob in decoded {
        if let index = blob.index { counts[index, default: 0] += 1 }
    }
    return decoded.map { blob in
        guard let index = blob.index, (counts[index] ?? 0) > 1 else { return blob }
        return DecodedBlob(index: nil, centroid: blob.centroid, stable: true)
    }
}

private func nearest(_ point: ImagePoint, _ points: [ImagePoint], _ radius: Double) -> Int? {
    var best: Int?
    var bestDistance = radius
    for (index, other) in points.enumerated() {
        let delta = hypot(point.x - other.x, point.y - other.y)
        if delta <= bestDistance {
            bestDistance = delta
            best = index
        }
    }
    return best
}

public struct CameraIntrinsics: Equatable {
    public var fx: Double
    public var fy: Double
    public var cx: Double
    public var cy: Double

    public init(fx: Double, fy: Double, cx: Double, cy: Double) {
        self.fx = fx
        self.fy = fy
        self.cx = cx
        self.cy = cy
    }
}

public struct CameraPose: Equatable {
    public var right: Vec3
    public var up: Vec3
    public var back: Vec3
    public var origin: Vec3

    public init(right: Vec3, up: Vec3, back: Vec3, origin: Vec3) {
        self.right = right
        self.up = up
        self.back = back
        self.origin = origin
    }

    public static let identity = CameraPose(right: Vec3(1, 0, 0), up: Vec3(0, 1, 0), back: Vec3(0, 0, 1), origin: .zero)
}

public struct Ray: Equatable {
    public var origin: Vec3
    public var direction: Vec3

    public init(origin: Vec3, direction: Vec3) {
        self.origin = origin
        self.direction = direction
    }
}

public func rayFromPixel(u: Double, v: Double, intrinsics: CameraIntrinsics, pose: CameraPose) -> Ray {
    let x = intrinsics.fx == 0 ? 0 : (u - intrinsics.cx) / intrinsics.fx
    let y = intrinsics.fy == 0 ? 0 : (v - intrinsics.cy) / intrinsics.fy
    let local = Vec3(x, -y, -1).normalized()
    let world = pose.right * local.x + pose.up * local.y + pose.back * local.z
    return Ray(origin: pose.origin, direction: world.normalized())
}

public struct Triangulation: Equatable {
    public var position: Vec3
    public var gap: Double
    public var alongA: Double
    public var alongB: Double
}

public func triangulate(_ first: Ray, _ second: Ray) -> Triangulation? {
    let d1 = first.direction.normalized()
    let d2 = second.direction.normalized()
    let gap0 = first.origin - second.origin
    let aa = d1.dot(d1)
    let bb = d1.dot(d2)
    let cc = d2.dot(d2)
    let dd = d1.dot(gap0)
    let ee = d2.dot(gap0)
    let denom = aa * cc - bb * bb
    if abs(denom) < 1e-10 { return nil }
    let alongA = (bb * ee - cc * dd) / denom
    let alongB = (aa * ee - bb * dd) / denom
    if alongA <= 1e-4 || alongB <= 1e-4 { return nil }
    let pointA = first.origin + d1 * alongA
    let pointB = second.origin + d2 * alongB
    return Triangulation(position: (pointA + pointB) * 0.5, gap: distance(pointA, pointB), alongA: alongA, alongB: alongB)
}

public struct SegmentHit: Equatable {
    public var position: Vec3
    public var segment: String
    public var distance: Double
    public var along: Double
}

public func closestOnSegment(_ ray: Ray, _ start: Vec3, _ end: Vec3) -> (point: Vec3, distance: Double, along: Double, fraction: Double)? {
    let direction = ray.direction.normalized()
    let span = end - start
    let length2 = span.dot(span)
    if length2 < 1e-12 {
        let along = (start - ray.origin).dot(direction)
        if along <= 1e-4 { return nil }
        let closest = ray.origin + direction * along
        return (start, distance(start, closest), along, 0)
    }
    let gap0 = ray.origin - start
    let aa = direction.dot(direction)
    let bb = direction.dot(span)
    let cc = length2
    let dd = direction.dot(gap0)
    let ee = span.dot(gap0)
    let denom = aa * cc - bb * bb
    var along: Double
    var fraction: Double
    if abs(denom) < 1e-12 {
        fraction = min(max(ee / cc, 0), 1)
        along = dd
    } else {
        along = (bb * ee - cc * dd) / denom
        fraction = (aa * ee - bb * dd) / denom
    }
    if fraction < 0 || fraction > 1 {
        fraction = min(max(fraction, 0), 1)
        let clamped = start + span * fraction
        along = (clamped - ray.origin).dot(direction)
    }
    if along <= 1e-4 { return nil }
    let point = start + span * fraction
    let closest = ray.origin + direction * along
    return (point, distance(point, closest), along, fraction)
}

public struct ModelSample: Equatable {
    public var index: Int
    public var position: Vec3
    public var segment: String

    public init(index: Int, position: Vec3, segment: String) {
        self.index = index
        self.position = position
        self.segment = segment
    }
}

public func refineOnModel(_ ray: Ray, _ samples: [ModelSample], maxDistance: Double) -> SegmentHit? {
    var best: SegmentHit?
    let direction = ray.direction.normalized()
    for index in samples.indices {
        let sample = samples[index]
        let hit: SegmentHit?
        if index + 1 < samples.count, samples[index + 1].segment == sample.segment {
            guard let found = closestOnSegment(ray, sample.position, samples[index + 1].position), found.distance <= maxDistance else { continue }
            hit = SegmentHit(position: found.point, segment: sample.segment, distance: found.distance, along: found.along)
        } else {
            let along = (sample.position - ray.origin).dot(direction)
            if along <= 1e-4 { continue }
            let closest = ray.origin + direction * along
            let gap = distance(sample.position, closest)
            if gap > maxDistance { continue }
            hit = SegmentHit(position: sample.position, segment: sample.segment, distance: gap, along: along)
        }
        guard let hit else { continue }
        if best == nil || hit.distance < best!.distance { best = hit }
    }
    return best
}

public struct Sighting: Equatable {
    public var index: Int
    public var ray: Ray

    public init(index: Int, ray: Ray) {
        self.index = index
        self.ray = ray
    }
}

public struct PixelAssessment: Equatable {
    public var index: Int
    public var position: Vec3?
    public var confidence: Double
    public var method: CorrectionMethod?
    public var viewpoints: Int
    public var outlier: Bool
    public var missing: Bool
    public var occluded: Bool
    public var segment: String
}

public func assessPixels(
    model: [ModelSample],
    viewpoints: [[Sighting]],
    outlierMetres: Double = 0.15,
    rayGapMetres: Double = 0.08,
    finished: Bool = false
) -> [PixelAssessment] {
    model.map { sample in
        let rays = viewpoints.flatMap { view in view.filter { $0.index == sample.index }.map(\.ray) }
        if rays.isEmpty {
            return PixelAssessment(
                index: sample.index, position: nil, confidence: 0, method: nil, viewpoints: 0,
                outlier: false, missing: !viewpoints.isEmpty && finished, occluded: !viewpoints.isEmpty && !finished,
                segment: sample.segment
            )
        }
        let triangulated = bestTriangulation(rays)
        let refined = (rays.count == 1 || triangulated == nil) ? refineOnModel(rays[0], model, maxDistance: 0.25) : nil
        if let triangulated, triangulated.gap <= rayGapMetres {
            let far = distance(triangulated.position, sample.position) > outlierMetres
            let confidence = max(0.2, min(1, 1 - triangulated.gap / rayGapMetres))
            return PixelAssessment(
                index: sample.index, position: triangulated.position, confidence: confidence, method: .triangulated,
                viewpoints: rays.count, outlier: far, missing: false, occluded: false, segment: sample.segment
            )
        }
        if let refined {
            let far = distance(refined.position, sample.position) > outlierMetres || refined.distance > 0.05
            let confidence = far ? 0.2 : max(0.35, min(1, 1 - refined.distance / 0.05))
            return PixelAssessment(
                index: sample.index, position: refined.position, confidence: confidence, method: .segment,
                viewpoints: rays.count, outlier: far, missing: false, occluded: false, segment: sample.segment
            )
        }
        if let triangulated {
            return PixelAssessment(
                index: sample.index, position: triangulated.position, confidence: 0.2, method: .triangulated,
                viewpoints: rays.count, outlier: true, missing: false, occluded: false, segment: sample.segment
            )
        }
        return PixelAssessment(
            index: sample.index, position: nil, confidence: 0, method: nil, viewpoints: rays.count,
            outlier: finished, missing: finished, occluded: !finished, segment: sample.segment
        )
    }
}

private func bestTriangulation(_ rays: [Ray]) -> Triangulation? {
    var best: Triangulation?
    guard rays.count >= 2 else { return nil }
    for left in 0..<(rays.count - 1) {
        for right in (left + 1)..<rays.count {
            guard let found = triangulate(rays[left], rays[right]) else { continue }
            if best == nil || found.gap < best!.gap { best = found }
        }
    }
    return best
}

public func applyingCorrections(
    _ fixture: Fixture,
    runId: String,
    assessments: [PixelAssessment],
    includeOutliers: Bool
) -> Fixture {
    var copy = fixture
    copy.schemaVersion = max(fixture.schemaVersion, 2)
    copy.pixelCorrections.removeAll { $0.runId == runId }
    let accepted = assessments.filter { item in
        item.position != nil && !item.missing && !item.occluded && (!item.outlier || includeOutliers)
    }
    for item in accepted {
        guard let position = item.position, let method = item.method else { continue }
        let stamp = String(runId.prefix(40))
        copy.pixelCorrections.append(
            PixelCorrection(
                id: "fix-\(stamp)-\(item.index)",
                runId: runId,
                index: item.index,
                position: position,
                confidence: min(max(item.confidence, 0), 1),
                method: method,
                viewpoints: max(item.viewpoints, 1)
            )
        )
    }
    return copy
}

public func applyingDetectedPath(
    _ fixture: Fixture,
    runId: String,
    assessments: [PixelAssessment],
    includeOutliers: Bool
) -> Fixture {
    var copy = applyingCorrections(fixture, runId: runId, assessments: assessments, includeOutliers: includeOutliers)
    guard let runIndex = copy.runs.firstIndex(where: { $0.id == runId }) else { return copy }
    let accepted = assessments
        .filter { $0.position != nil && !$0.missing && !$0.occluded && (!$0.outlier || includeOutliers) }
        .sorted { $0.index < $1.index }
    guard accepted.count >= 2 else { return copy }
    let run = copy.runs[runIndex]
    let usedElsewhere = Set(copy.runs.filter { $0.id != runId }.flatMap { $0.segments.flatMap(\.waypointIds) })
    let removed = Set(run.segments.flatMap(\.waypointIds)).subtracting(usedElsewhere)
    copy.waypoints.removeAll { removed.contains($0.id) }
    let points = accepted.compactMap(\.position)
    let stamp = String(runId.prefix(40))
    let waypoints = accepted.enumerated().map { _, item in
        Waypoint(
            id: "d-\(stamp)-\(item.index)",
            position: item.position ?? .zero,
            source: .detected,
            confidence: min(max(item.confidence, 0), 1),
            label: nil
        )
    }
    copy.waypoints.append(contentsOf: waypoints)
    var updated = run
    updated.segments = [
        Segment(
            id: run.segments.first?.id ?? "detected-path",
            kind: .polyline,
            waypointIds: waypoints.map(\.id),
            params: SegmentParams(),
            pixelCountOverride: nil
        )
    ]
    let length = polylineLength(points)
    if run.pixelCount > 0 && length > 1e-6 {
        updated.pitchMm = length / Double(run.pixelCount) * 1000
    }
    copy.runs[runIndex] = updated
    return copy
}
