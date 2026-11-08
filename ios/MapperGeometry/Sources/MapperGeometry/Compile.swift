import Foundation

public enum CompileError: Error, CustomStringConvertible {
    case message(String)
    public var description: String {
        switch self {
        case .message(let text): return text
        }
    }
}

public func compile(_ fixture: Fixture) throws -> [MappedPixel] {
    let index = Dictionary(uniqueKeysWithValues: fixture.waypoints.map { ($0.id, $0) })
    var pixels: [MappedPixel] = []
    for run in fixture.runs {
        pixels.append(contentsOf: try compileRun(fixture.name, run, index))
    }
    return pixels
}

public func validateCounts(_ fixture: Fixture, pixels: [MappedPixel]? = nil) throws -> [RunReport] {
    let compiled = try pixels ?? compile(fixture)
    return fixture.runs.map { run in
        let segmentIds = Set(run.segments.map(\.id))
        let placed = compiled.filter { segmentIds.contains($0.segment) && $0.controller == run.controllerId }.count
        let difference = abs(placed - run.pixelCount)
        let ratio = run.pixelCount == 0 ? 0 : Double(difference) / Double(run.pixelCount)
        return RunReport(runId: run.id, expected: run.pixelCount, placed: placed, ok: run.pixelCount == 0 || ratio <= 0.03)
    }
}

public func normalise(_ fixture: Fixture) -> Fixture {
    var copy = fixture
    let moved = normalisePoints(copy.waypoints.map(\.position))
    for index in copy.waypoints.indices {
        copy.waypoints[index].position = moved.points[index]
    }
    copy.frame = moved.frame
    return copy
}

private func compileRun(_ name: String, _ run: Run, _ waypoints: [String: Waypoint]) throws -> [MappedPixel] {
    let pitch = run.pitchMm / 1000
    if pitch <= 0 { throw CompileError.message("Pitch has to be greater than zero.") }
    var pixels: [MappedPixel] = []
    var ordinal = 0
    var cursor = 0.0
    var index = run.startIndex
    for segment in run.segments {
        let points = try pointsFor(segment, waypoints)
        let length = try segmentLength(segment, points)
        if segment.kind == .jump { continue }
        if let override = segment.pixelCountOverride {
            // Place exactly N pixels on this span. The pitch clock then jumps to the
            // geometric end, so the next span is spaced as if this count had not changed.
            if override > 0 {
                for slot in 0..<override {
                    let fraction = Double(slot) / Double(override)
                    let point = try positionAt(segment, points, fraction)
                    pixels.append(pixel(name, run, segment, index, point, fraction))
                    index += 1
                }
            }
            cursor += length
            ordinal = pitchOrdinal(cursor, pitch)
            continue
        }
        let segmentEnd = cursor + length
        while Double(ordinal) * pitch < segmentEnd - 1e-9 {
            if ordinal > 100_000 { throw CompileError.message("That path would place more than 100000 pixels.") }
            let along = Double(ordinal) * pitch
            var fraction = length < 1e-9 ? 0 : (along - cursor) / length
            fraction = min(max(fraction, 0), 1)
            let point = try positionAt(segment, points, fraction)
            pixels.append(pixel(name, run, segment, index, point, fraction))
            ordinal += 1
            index += 1
        }
        cursor = segmentEnd
    }
    return pixels
}

private func pitchOrdinal(_ cursor: Double, _ pitch: Double) -> Int {
    if pitch <= 0 { return 0 }
    return Int(ceil((cursor - 1e-9) / pitch))
}

private func pointsFor(_ segment: Segment, _ waypoints: [String: Waypoint]) throws -> [Vec3] {
    var points: [Vec3] = []
    for id in segment.waypointIds {
        guard let waypoint = waypoints[id] else {
            throw CompileError.message("Segment \(segment.id) references unknown waypoint \(id).")
        }
        points.append(waypoint.position)
    }
    if points.count < 2 { throw CompileError.message("Segment \(segment.id) needs at least two waypoints.") }
    return points
}

private struct HelixArgs {
    var start: Vec3
    var end: Vec3
    var radiusStart: Double
    var radiusEnd: Double
    var turns: Double
    var handedness: String
    var hint: Vec3?
}

private func helixArgs(_ segment: Segment, _ points: [Vec3]) throws -> HelixArgs {
    let handedness = segment.params.handedness == "cw" ? "cw" : "ccw"
    if let turns = segment.params.turns, let startMm = segment.params.radiusStartMm, let endMm = segment.params.radiusEndMm {
        let hint = points.count >= 3 ? points[1] - points[0] : nil
        return HelixArgs(
            start: points[0], end: points[points.count - 1],
            radiusStart: startMm / 1000, radiusEnd: endMm / 1000,
            turns: turns, handedness: handedness, hint: hint
        )
    }
    let fitted = try fitHelix(points)
    let hint = points.count >= 3 ? points[1] - points[0] : nil
    return HelixArgs(
        start: fitted.axisStart, end: fitted.axisEnd,
        radiusStart: fitted.radiusStart, radiusEnd: fitted.radiusEnd,
        turns: fitted.turns, handedness: handedness, hint: hint
    )
}

private func segmentLength(_ segment: Segment, _ points: [Vec3]) throws -> Double {
    switch segment.kind {
    case .straight, .jump:
        return distance(points[0], points[1])
    case .polyline:
        return polylineLength(points)
    case .curve:
        return curveLength(points)
    case .helix:
        let args = try helixArgs(segment, points)
        return helixArcLength(
            axisStart: args.start, axisEnd: args.end, radiusStart: args.radiusStart, radiusEnd: args.radiusEnd,
            turns: args.turns, handedness: args.handedness, hint: args.hint
        )
    }
}

private func positionAt(_ segment: Segment, _ points: [Vec3], _ fraction: Double) throws -> Vec3 {
    switch segment.kind {
    case .straight, .jump:
        return positionOnPolyline([points[0], points[1]], fraction)
    case .polyline:
        return positionOnPolyline(points, fraction)
    case .curve:
        return curvePosition(points, fraction)
    case .helix:
        let args = try helixArgs(segment, points)
        return helixPosition(
            axisStart: args.start, axisEnd: args.end, radiusStart: args.radiusStart, radiusEnd: args.radiusEnd,
            turns: args.turns, fraction: fraction, handedness: args.handedness, hint: args.hint
        )
    }
}

private func pixel(_ name: String, _ run: Run, _ segment: Segment, _ index: Int, _ point: Vec3, _ fraction: Double) -> MappedPixel {
    MappedPixel(
        fixture: name, controller: run.controllerId, output: run.outputIndex, index: index,
        x: point.x, y: point.y, z: point.z, segment: segment.id, u: fraction
    )
}
