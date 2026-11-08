import Foundation

public struct DriftResult: Equatable {
    public var corrected: [Vec3]
    public var error: Vec3
    public var errorM: Double
    public var weights: [Double]
}

public func yawMatrix(_ yaw: Double) -> [Double] {
    let cosine = cos(yaw)
    let sine = sin(yaw)
    return [cosine, 0, sine, 0, 1, 0, -sine, 0, cosine]
}

public func applyRotation(_ rotation: [Double], _ point: Vec3) -> Vec3 {
    Vec3(
        rotation[0] * point.x + rotation[1] * point.y + rotation[2] * point.z,
        rotation[3] * point.x + rotation[4] * point.y + rotation[5] * point.z,
        rotation[6] * point.x + rotation[7] * point.y + rotation[8] * point.z
    )
}

public func rigidFromFrame(
    _ frame: FrameTransform,
    method: AlignmentMethod = .worldMap,
    referenceIds: [String] = []
) -> SessionAlignment {
    let rotation = yawMatrix(frame.yawRadians)
    let translation = applyRotation(rotation, frame.translation)
    return SessionAlignment(
        method: method,
        rotation: rotation,
        translation: translation,
        yawRadians: frame.yawRadians,
        residualM: 0,
        referenceIds: referenceIds
    )
}

public func distributeDrift(_ points: [Vec3], retap: Vec3, referenceIndex: Int = 0) throws -> DriftResult {
    if referenceIndex < 0 || referenceIndex >= points.count {
        throw GeometryError("The reference tap is not on this path.")
    }
    let reference = points[referenceIndex]
    let error = reference - retap
    var walked = 0.0
    var distances = Array(repeating: 0.0, count: points.count)
    if referenceIndex + 1 < points.count {
        for index in (referenceIndex + 1)..<points.count {
            walked += distance(points[index - 1], points[index])
            distances[index] = walked
        }
    }
    let total = walked + distance(points[points.count - 1], retap)
    var corrected: [Vec3] = []
    var weights: [Double] = []
    for index in points.indices {
        let weight = index < referenceIndex || total < 1e-9 ? 0 : distances[index] / total
        weights.append(weight)
        corrected.append(points[index] + error * weight)
    }
    return DriftResult(corrected: corrected, error: error, errorM: error.length(), weights: weights)
}

public func alignSessions(_ source: [Vec3], _ target: [Vec3], referenceIds: [String] = []) throws -> SessionAlignment {
    if source.count < 2 || source.count != target.count {
        throw GeometryError("Alignment needs two or three matching marks.")
    }
    let solved: (rotation: [Double], translation: Vec3, yaw: Double, method: AlignmentMethod)
    if source.count == 2 {
        let yawFit = yawTranslation(source, target)
        solved = (yawFit.rotation, yawFit.translation, yawFit.yaw, .yawTranslation)
    } else {
        let fit = kabsch(source, target)
        solved = (fit.rotation, fit.translation, yawOf(fit.rotation), .kabsch)
    }
    return SessionAlignment(
        method: solved.method,
        rotation: solved.rotation,
        translation: solved.translation,
        yawRadians: solved.yaw,
        residualM: residual(source, target, solved.rotation, solved.translation),
        referenceIds: referenceIds
    )
}

public func outwardParameters(_ points: [Vec3], role: String, trunkPoints: [Vec3]) -> [(Double, Double)] {
    if points.isEmpty { return [] }
    if points.count == 1 { return [(0, 0)] }
    let rootFirst = rootIsFirst(points, role, trunkPoints)
    var cumulative = [0.0]
    for index in 1..<points.count {
        cumulative.append(cumulative[index - 1] + distance(points[index - 1], points[index]))
    }
    let total = cumulative[cumulative.count - 1]
    let lowest = points.map(\.y).min()!
    return zip(points, cumulative).map { point, walked in
        let wire = total < 1e-9 ? 0 : walked / total
        let outward = rootFirst ? wire : 1 - wire
        return (outward, point.y - lowest)
    }
}

public func deriveReferences(_ waypoints: [Waypoint]) -> [ReferencePoint] {
    guard waypoints.count >= 2 else { return [] }
    let labelled = waypoints.filter { !($0.label ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var pool = labelled.count >= 2 ? labelled : waypoints
    if pool.count > 3 {
        pool = [pool[0], pool[pool.count / 2], pool[pool.count - 1]]
    } else if pool.count == 2, waypoints.count >= 3 {
        let middle = waypoints[waypoints.count / 2]
        if middle.id != pool[0].id, middle.id != pool[1].id {
            pool = [pool[0], middle, pool[1]]
        }
    }
    return pool.prefix(3).enumerated().map { index, waypoint in
        let raw = (waypoint.label ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let label = raw.isEmpty ? "Mark \(index + 1)" : String(raw.prefix(80))
        var referenceId = "ref-\(waypoint.id)"
        if referenceId.count > 80 {
            referenceId = String(waypoint.id.prefix(80))
        }
        return ReferencePoint(id: referenceId, label: label, position: waypoint.position, waypointId: waypoint.id)
    }
}

private func yawTranslation(_ source: [Vec3], _ target: [Vec3]) -> (rotation: [Double], translation: Vec3, yaw: Double) {
    let sourceSpan = source[1] - source[0]
    let targetSpan = target[1] - target[0]
    let yaw: Double
    if hypot(sourceSpan.x, sourceSpan.z) < 1e-6 || hypot(targetSpan.x, targetSpan.z) < 1e-6 {
        yaw = 0
    } else {
        let raw = atan2(targetSpan.x, targetSpan.z) - atan2(sourceSpan.x, sourceSpan.z)
        yaw = atan2(sin(raw), cos(raw))
    }
    let rotation = yawMatrix(yaw)
    let translation = centroid(target) - applyRotation(rotation, centroid(source))
    return (rotation, translation, yaw)
}

private func yawOf(_ rotation: [Double]) -> Double {
    atan2(rotation[2], rotation[0])
}

private func residual(_ source: [Vec3], _ target: [Vec3], _ rotation: [Double], _ translation: Vec3) -> Double {
    guard !source.isEmpty else { return 0 }
    var total = 0.0
    for index in source.indices {
        total += distance(applyRotation(rotation, source[index]) + translation, target[index]) * distance(applyRotation(rotation, source[index]) + translation, target[index])
    }
    let value = (total / Double(source.count)).squareRoot()
    return value < 1e-12 ? 0 : value
}

private func rootIsFirst(_ points: [Vec3], _ role: String, _ trunkPoints: [Vec3]) -> Bool {
    let first = points[0]
    let last = points[points.count - 1]
    if role == "branch", !trunkPoints.isEmpty {
        let nearFirst = trunkPoints.map { distance(first, $0) }.min()!
        let nearLast = trunkPoints.map { distance(last, $0) }.min()!
        if abs(nearFirst - nearLast) > 1e-6 {
            return nearFirst < nearLast
        }
    }
    if abs(first.y - last.y) <= 1e-6 { return true }
    return first.y < last.y
}

private func kabsch(_ source: [Vec3], _ target: [Vec3]) -> (rotation: [Double], translation: Vec3) {
    let sourceCenter = centroid(source)
    let targetCenter = centroid(target)
    var covariance = Array(repeating: Array(repeating: 0.0, count: 3), count: 3)
    for index in source.indices {
        let deltaS = source[index] - sourceCenter
        let deltaT = target[index] - targetCenter
        let sourceCoords = [deltaS.x, deltaS.y, deltaS.z]
        let targetCoords = [deltaT.x, deltaT.y, deltaT.z]
        for row in 0..<3 {
            for column in 0..<3 {
                covariance[row][column] += sourceCoords[row] * targetCoords[column]
            }
        }
    }
    let svd = svd3(covariance)
    var vt = svd.vt
    var rotation = matmul(transpose(vt), transpose(svd.left))
    if determinant(rotation) < 0 {
        vt[2] = vt[2].map { -$0 }
        rotation = matmul(transpose(vt), transpose(svd.left))
    }
    let flat = rotation.flatMap { $0 }
    return (flat, targetCenter - applyRotation(flat, sourceCenter))
}

private func svd3(_ matrix: [[Double]]) -> (left: [[Double]], singular: [Double], vt: [[Double]]) {
    let eigen = jacobiSymmetric(matmul(transpose(matrix), matrix))
    let singular = eigen.values.map { max($0, 0).squareRoot() }
    var columns: [[Double]?] = []
    for index in 0..<3 {
        let axis = (0..<3).map { eigen.vectors[$0][index] }
        let projected = matvec(matrix, axis)
        if singular[index] > 1e-12 {
            columns.append(projected.map { $0 / singular[index] })
        } else {
            columns.append(nil)
        }
    }
    let filled = orthonormalize(completeBasis(columns))
    return (columnsToRows(filled), singular, transpose(eigen.vectors))
}

private func jacobiSymmetric(_ matrix: [[Double]]) -> (values: [Double], vectors: [[Double]]) {
    var working = matrix.map { $0 }
    var basis = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in 0..<32 {
        var pivotRow = 0
        var pivotColumn = 1
        var largest = abs(working[0][1])
        for pair in [(0, 2), (1, 2)] {
            let magnitude = abs(working[pair.0][pair.1])
            if magnitude > largest {
                largest = magnitude
                pivotRow = pair.0
                pivotColumn = pair.1
            }
        }
        if largest < 1e-15 { break }
        let diagonalRow = working[pivotRow][pivotRow]
        let diagonalColumn = working[pivotColumn][pivotColumn]
        let off = working[pivotRow][pivotColumn]
        let cosine: Double
        let sine: Double
        if abs(off) < 1e-20 {
            cosine = 1
            sine = 0
        } else {
            let tau = (diagonalColumn - diagonalRow) / (2 * off)
            let tangent = tau >= 0
                ? 1 / (tau + (1 + tau * tau).squareRoot())
                : -1 / (-tau + (1 + tau * tau).squareRoot())
            cosine = 1 / (1 + tangent * tangent).squareRoot()
            sine = tangent * cosine
        }
        for row in 0..<3 where row != pivotRow && row != pivotColumn {
            let rowPivot = working[row][pivotRow]
            let rowColumn = working[row][pivotColumn]
            let nextRow = cosine * rowPivot - sine * rowColumn
            let nextColumn = sine * rowPivot + cosine * rowColumn
            working[row][pivotRow] = nextRow
            working[pivotRow][row] = nextRow
            working[row][pivotColumn] = nextColumn
            working[pivotColumn][row] = nextColumn
        }
        working[pivotRow][pivotRow] = cosine * cosine * diagonalRow - 2 * sine * cosine * off + sine * sine * diagonalColumn
        working[pivotColumn][pivotColumn] = sine * sine * diagonalRow + 2 * sine * cosine * off + cosine * cosine * diagonalColumn
        working[pivotRow][pivotColumn] = 0
        working[pivotColumn][pivotRow] = 0
        for row in 0..<3 {
            let basisRow = basis[row][pivotRow]
            let basisColumn = basis[row][pivotColumn]
            basis[row][pivotRow] = cosine * basisRow - sine * basisColumn
            basis[row][pivotColumn] = sine * basisRow + cosine * basisColumn
        }
    }
    let values = [working[0][0], working[1][1], working[2][2]]
    let order = [0, 1, 2].sorted { values[$0] > values[$1] }
    let sortedValues = order.map { values[$0] }
    let sortedBasis = (0..<3).map { row in order.map { basis[row][$0] } }
    return (sortedValues, sortedBasis)
}

private func completeBasis(_ columns: [[Double]?]) -> [[Double]] {
    var result = columns.map { column -> [Double]? in
        guard let column else { return nil }
        return unit(column)
    }
    for index in result.indices where result[index] == nil {
        let known = result.compactMap { $0 }
        if known.isEmpty {
            result[index] = [1, 0, 0]
        } else if known.count == 1 {
            let helper = abs(known[0][0]) < 0.9 ? [1.0, 0.0, 0.0] : [0.0, 1.0, 0.0]
            result[index] = unit(cross(known[0], helper))
        } else {
            result[index] = unit(cross(known[0], known[1]))
        }
    }
    return result.map { $0 ?? [1, 0, 0] }
}

private func orthonormalize(_ columns: [[Double]]) -> [[Double]] {
    var kept: [[Double]] = []
    for column in columns {
        var vector = column
        for previous in kept {
            let scale = zip(vector, previous).reduce(0) { $0 + $1.0 * $1.1 }
            vector = zip(vector, previous).map { $0 - scale * $1 }
        }
        kept.append(unit(vector))
    }
    return kept
}

private func unit(_ vector: [Double]) -> [Double] {
    let length = vector.reduce(0) { $0 + $1 * $1 }.squareRoot()
    if length < 1e-15 { return [1, 0, 0] }
    return vector.map { $0 / length }
}

private func cross(_ left: [Double], _ right: [Double]) -> [Double] {
    [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]
}

private func transpose(_ matrix: [[Double]]) -> [[Double]] {
    (0..<matrix[0].count).map { column in matrix.map { $0[column] } }
}

private func matmul(_ left: [[Double]], _ right: [[Double]]) -> [[Double]] {
    (0..<3).map { row in
        (0..<3).map { column in
            (0..<3).reduce(0) { $0 + left[row][$1] * right[$1][column] }
        }
    }
}

private func matvec(_ matrix: [[Double]], _ vector: [Double]) -> [Double] {
    matrix.map { row in zip(row, vector).reduce(0) { $0 + $1.0 * $1.1 } }
}

private func columnsToRows(_ columns: [[Double]]) -> [[Double]] {
    (0..<3).map { row in (0..<3).map { columns[$0][row] } }
}

private func determinant(_ matrix: [[Double]]) -> Double {
    matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
}
