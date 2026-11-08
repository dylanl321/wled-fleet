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

public struct EffectAddress: Equatable {
    public var target: String
    public var heightM: Double?
    public var branchHeightM: Double?
    public var branchU: Double?
    public var bandM: Double?
    public var bandU: Double?

    public init(
        target: String = "all",
        heightM: Double? = nil,
        branchHeightM: Double? = nil,
        branchU: Double? = nil,
        bandM: Double? = nil,
        bandU: Double? = nil
    ) {
        self.target = target
        self.heightM = heightM
        self.branchHeightM = branchHeightM
        self.branchU = branchU
        self.bandM = bandM
        self.bandU = bandU
    }
}

public struct EffectError: Error, CustomStringConvertible {
    public var description: String
    public init(_ description: String) { self.description = description }
}

private let cometWidth = 0.18

public func effectColors(_ pixels: [MappedPixel], _ name: EffectName, phase: Double, address: EffectAddress = EffectAddress()) throws -> [RGB] {
    try requireTarget(pixels, address.target)
    guard !pixels.isEmpty else { return [] }
    var cycle = phase.truncatingRemainder(dividingBy: 1)
    if cycle < 0 { cycle += 1 }
    if phase > 0 && abs(cycle) < 1e-12 { cycle = 1 }
    let keep = try pixels.map { try addressed($0, address) }
    let chosen = zip(pixels, keep).compactMap { pixel, on -> MappedPixel? in on ? pixel : nil }
    let low = chosen.map(\.y).min() ?? 0
    let high = chosen.map(\.y).max() ?? 0
    let height = max(high - low, 1e-6)
    return zip(pixels, keep).map { pixel, on in
        guard on else { return RGB(0, 0, 0) }
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
        case .branchChase:
            return comet(pixel.branchU, cycle)
        }
    }
}

private func comet(_ branchU: Double, _ phase: Double) -> RGB {
    let delta = phase - branchU
    if delta < -1e-9 || delta > cometWidth { return RGB(0, 0, 0) }
    let gain = 1 - max(delta, 0) / cometWidth
    return RGB(Int((255 * gain).rounded()), Int((244 * gain).rounded()), Int((230 * gain).rounded()))
}

private func requireTarget(_ pixels: [MappedPixel], _ target: String) throws {
    let key = targetKey(target)
    if key == "all" { return }
    if pixels.contains(where: { matches($0, key) }) { return }
    throw EffectError("Nothing on this fixture matches that.")
}

private func addressed(_ pixel: MappedPixel, _ address: EffectAddress) throws -> Bool {
    if !matches(pixel, targetKey(address.target)) { return false }
    if try !near(pixel.y, address.heightM, address.bandM, 0.05) { return false }
    if try !near(pixel.branchHeight, address.branchHeightM, address.bandM, 0.05) { return false }
    if try !near(pixel.branchU, address.branchU, address.bandU, 0.05) { return false }
    return true
}

private func near(_ value: Double, _ center: Double?, _ band: Double?, _ defaultBand: Double) throws -> Bool {
    guard let center else { return true }
    let width = band ?? defaultBand
    if width < 0 { throw EffectError("The band has to be zero or more.") }
    return abs(value - center) <= width + 1e-9
}

private func targetKey(_ value: String) -> String {
    let key = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    if key.isEmpty || key == "all" { return "all" }
    if key == "branches" || key == "all branches" { return "branches" }
    if key == "trunk" || key == "the trunk" { return "trunk" }
    return key
}

private func matches(_ pixel: MappedPixel, _ target: String) -> Bool {
    switch target {
    case "all":
        return true
    case "branches":
        return pixel.groupRole == "branch"
    case "trunk":
        return pixel.groupRole == "trunk"
    default:
        return target == pixel.group.lowercased() || (!pixel.groupName.isEmpty && target == pixel.groupName.lowercased())
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
