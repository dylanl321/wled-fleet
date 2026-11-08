import Foundation

public struct RGB: Equatable {
    public var r: Int
    public var g: Int
    public var b: Int
    public init(_ r: Int, _ g: Int, _ b: Int) {
        self.r = r
        self.g = g
        self.b = b
    }
}

public func targetedColors(
    _ pixels: [MappedPixel],
    _ name: EffectName,
    phase: Double,
    fixture: Fixture? = nil,
    branch: String? = nil,
    segment: String? = nil,
    height: Double? = nil,
    band: Double = 0.15
) -> [RGB] {
    let chosen = pixels.indices.filter { selected(pixels[$0], fixture: fixture, branch: branch, segment: segment, height: height, band: band) }
    var output = Array(repeating: RGB(0, 0, 0), count: pixels.count)
    guard !chosen.isEmpty else { return output }
    let colors = effectColors(chosen.map { pixels[$0] }, name, phase: phase)
    for (slot, index) in chosen.enumerated() {
        output[index] = colors[slot]
    }
    return output
}

private func selected(_ pixel: MappedPixel, fixture: Fixture?, branch: String?, segment: String?, height: Double?, band: Double) -> Bool {
    if let segment, !segment.isEmpty, pixel.segment != segment { return false }
    if let branch, !branch.isEmpty, !branchMatch(pixel.segment, branch, fixture) { return false }
    if let height, abs(pixel.y - height) > band { return false }
    return true
}

private func branchMatch(_ segmentId: String, _ branch: String, _ fixture: Fixture?) -> Bool {
    let needle = branch.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    if needle.isEmpty || needle == segmentId.lowercased() { return true }
    guard let fixture else { return segmentId.lowercased().contains(needle) }
    let labels = Dictionary(uniqueKeysWithValues: fixture.waypoints.map { ($0.id, $0.label ?? "") })
    for run in fixture.runs {
        for segment in run.segments where segment.id == segmentId {
            let names = segment.waypointIds.map { labels[$0] ?? "" }
            let blob = ([segment.id] + names).joined(separator: " ").lowercased()
            return blob.contains(needle)
        }
    }
    return segmentId.lowercased().contains(needle)
}

public func effectColors(_ pixels: [MappedPixel], _ name: EffectName, phase: Double) -> [RGB] {
    guard !pixels.isEmpty else { return [] }
    var cycle = phase.truncatingRemainder(dividingBy: 1)
    if cycle < 0 { cycle += 1 }
    if phase > 0 && abs(cycle) < 1e-12 { cycle = 1 }
    let low = pixels.map(\.y).min()!
    let high = pixels.map(\.y).max()!
    let height = max(high - low, 1e-6)
    return pixels.map { pixel in
        switch name {
        case .fillBottom:
            let threshold = low + cycle * height
            return pixel.y <= threshold + 1e-9 ? RGB(255, 244, 230) : RGB(0, 0, 0)
        case .heightRainbow:
            let hue = ((pixel.y - low) / height + cycle).truncatingRemainder(dividingBy: 1)
            return hsv(hue < 0 ? hue + 1 : hue)
        case .angleSpin:
            var hue = (atan2(pixel.x, pixel.z) / (2 * Double.pi) + 0.5 + cycle).truncatingRemainder(dividingBy: 1)
            if hue < 0 { hue += 1 }
            return hsv(hue)
        }
    }
}

private func hsv(_ hue: Double) -> RGB {
    let sector = hue * 6
    let part = sector - floor(sector)
    let rising = Int((part * 255).rounded())
    let falling = Int(((1 - part) * 255).rounded())
    switch Int(floor(sector)) % 6 {
    case 0: return RGB(255, rising, 0)
    case 1: return RGB(falling, 255, 0)
    case 2: return RGB(0, 255, rising)
    case 3: return RGB(0, falling, 255)
    case 4: return RGB(rising, 0, 255)
    default: return RGB(255, 0, falling)
    }
}
