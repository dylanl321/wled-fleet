import Foundation
import XCTest
@testable import MapperGeometry

final class GeometryTests: XCTestCase {
    func testRegressionFixtures() throws {
        let straight = try fixture("straight-1m.json")
        let straightPixels = try compile(straight)
        XCTAssertEqual(straightPixels.count, 60)
        assertClose(straightPixels[0].y, 0)
        assertClose(straightPixels[30].y, 0.5)
        assertClose(straightPixels[59].y, 59.0 / 60.0)
        XCTAssertEqual(try validateCounts(straight, pixels: straightPixels).first?.ok, true)

        let wall = try fixture("wall-outline.json")
        let wallPixels = try compile(wall)
        XCTAssertEqual(wallPixels.count, 180)
        assertClose(wallPixels[60].x, 1)
        assertClose(wallPixels[60].y, 0)
        assertClose(wallPixels[90].x, 1)
        assertClose(wallPixels[90].y, 0.5)
        assertClose(wallPixels[150].x, 0)
        assertClose(wallPixels[150].y, 0.5)
        assertClose(wallPixels[60].u, 1.0 / 3.0)

        let tree = try fixture("branching-tree.json")
        let treePixels = try compile(tree)
        XCTAssertEqual(Set(treePixels.map(\.controller)), ["d1:67:00:00:00:07"])
        let trunk = treePixels.filter { $0.output == 0 }
        let branches = treePixels.filter { $0.output == 1 }
        XCTAssertEqual(trunk.count, 141)
        XCTAssertEqual(branches.count, 102)
        assertClose(trunk[0].x, 0.08)
        assertClose(trunk[0].y, 0)
        assertClose(trunk[0].z, 0)
        XCTAssertEqual(branches[0].segment, "seg-branch-1")
        assertClose(branches[42].x, -0.8)
        assertClose(branches[42].y, 1.8)
        let reports = try validateCounts(tree, pixels: treePixels)
        XCTAssertTrue(reports.allSatisfy(\.ok))
    }

    func testPixelCountOverrideDoesNotShiftTheNextSegment() throws {
        for override in [10, 100] {
            var fixture = try fixture("straight-1m.json")
            fixture.waypoints.append(
                Waypoint(id: "11111111-1111-4111-8111-000000000003", position: Vec3(0, 2, 0), source: .typed, confidence: 1, label: "Top")
            )
            fixture.runs[0].segments[0].pixelCountOverride = override
            fixture.runs[0].segments.append(
                Segment(
                    id: "upper",
                    kind: .straight,
                    waypointIds: ["11111111-1111-4111-8111-000000000002", "11111111-1111-4111-8111-000000000003"],
                    params: SegmentParams()
                )
            )
            let pixels = try compile(fixture)
            let first = pixels.filter { $0.segment == "seg-straight" }
            let upper = pixels.filter { $0.segment == "upper" }
            XCTAssertEqual(first.count, override)
            XCTAssertEqual(upper.count, 60)
            assertClose(first[0].y, 0)
            assertClose(first[first.count - 1].y, Double(override - 1) / Double(override))
            assertClose(upper[0].y, 1)
            assertClose(upper[1].y, 1 + 1.0 / 60.0)
            assertClose(upper[59].y, 1 + 59.0 / 60.0)
            XCTAssertEqual(upper.map(\.index), Array(override..<(override + 60)))
            let heights = upper.map(\.y)
            XCTAssertEqual(heights, heights.sorted())
            XCTAssertEqual(Set(heights.map { ($0 * 1_000_000).rounded() / 1_000_000 }).count, 60)
        }
    }

    func testSchemaAcceptsFixturesAndRejectsATypo() throws {
        let schema = try Data(contentsOf: repoRoot().appendingPathComponent("schema/fixture.v1.json"))
        for name in ["straight-1m.json", "wall-outline.json", "branching-tree.json", "two-controllers.json"] {
            let data = try Data(contentsOf: repoRoot().appendingPathComponent("fixtures/\(name)"))
            let errors = try SchemaCheck.validateFixture(data, schemaData: schema)
            XCTAssertEqual(errors, [], name)
        }
        var object = try JSONSerialization.jsonObject(with: Data(contentsOf: repoRoot().appendingPathComponent("fixtures/straight-1m.json"))) as! [String: Any]
        object["surfaceHint"] = "mesh"
        let schemaObject = try JSONSerialization.jsonObject(with: schema) as! [String: Any]
        let errors = SchemaCheck.errors(in: object, schema: schemaObject)
        XCTAssertTrue(errors.contains { $0.contains("surfaceHint") })
        object = try JSONSerialization.jsonObject(with: Data(contentsOf: repoRoot().appendingPathComponent("fixtures/straight-1m.json"))) as! [String: Any]
        object["extra"] = true
        let extra = SchemaCheck.errors(in: object, schema: schemaObject)
        XCTAssertTrue(extra.contains { $0.contains("extra") })
    }

    func testHelixFormulaHandednessAndFit() throws {
        let rise = 1.2
        let radius = 0.08
        let turns = 4.0
        let expected = turns * ((2 * Double.pi * radius) * (2 * Double.pi * radius) + 0.3 * 0.3).squareRoot()
        assertClose(helixLength(turns: turns, radius: radius, rise: rise), expected)
        let quarter = helixPosition(
            axisStart: Vec3(0, 0, 0), axisEnd: Vec3(0, rise, 0),
            radiusStart: radius, radiusEnd: radius, turns: turns, fraction: 0.25
        )
        assertClose(quarter.x, 0.08)
        assertClose(quarter.y, 0.3)
        assertClose(quarter.z, 0, tol: 1e-9)
        let ccw = helixPosition(axisStart: .zero, axisEnd: Vec3(0, rise, 0), radiusStart: radius, radiusEnd: radius, turns: turns, fraction: 0.01, handedness: "ccw")
        let cw = helixPosition(axisStart: .zero, axisEnd: Vec3(0, rise, 0), radiusStart: radius, radiusEnd: radius, turns: turns, fraction: 0.01, handedness: "cw")
        // Looking up (+Y), moving from +X toward -Z is counter-clockwise.
        XCTAssertGreaterThan(ccw.x, 0)
        XCTAssertLessThan(ccw.z, 0)
        XCTAssertGreaterThan(cw.z, 0)

        var taps: [Vec3] = []
        for (index, angle) in [0.0, Double.pi / 2, Double.pi, 3 * Double.pi / 2].enumerated() {
            taps.append(Vec3(0.1 * cos(angle), Double(index) * 0.3, 0.1 * sin(angle)))
        }
        let fitted = try fitHelix(taps)
        XCTAssertEqual(fitted.turns, 3)
        assertClose(fitted.axisStart.x, 0, tol: 1e-6)
        assertClose(fitted.radiusStart, 0.1, tol: 1e-6)
        let tapsLine = (0..<4).map { Vec3(0.2, Double($0) * 0.4, 0) }
        let bare = try fitHelix(tapsLine)
        assertClose(bare.radiusStart, 0, tol: 1e-6)
        let line = try fitHelix(tapsLine, radius: 0.08, inward: Vec3(-1, 0, 0))
        XCTAssertEqual(line.turns, 3)
        assertClose(line.radiusStart, 0.08)
        assertClose(line.axisStart.x, 0.12)
    }

    func testCurveEndsNormaliseAndNudge() throws {
        let points = [Vec3(0, 0, 0), Vec3(0, 0.2, 0.4), Vec3(0, 0.6, -0.2), Vec3(0, 1, 0)]
        let start = curvePosition(points, 0)
        let end = curvePosition(points, 1)
        assertClose(start.x, 0)
        assertClose(start.y, 0)
        assertClose(end.y, 1)
        assertClose(end.z, 0)
        XCTAssertGreaterThan(curveLength(points), distance(points[0], points[3]))

        let moved = normalisePoints([Vec3(1, 2, 3), Vec3(1, 5, 3), Vec3(3, 2, 3)])
        assertClose(moved.points[0].y, 0)
        assertClose(moved.points[0].x, 0, tol: 1e-9)
        XCTAssertGreaterThan(moved.points[0].z, 0)
        let meanX = moved.points.map(\.x).reduce(0, +) / 3
        assertClose(meanX, 0, tol: 1e-9)

        var ring: [(Double, Double)] = []
        for step in 0..<16 {
            let angle = Double(step) / 16 * 2 * Double.pi
            ring.append((0.08 * cos(angle), 0.08 * sin(angle)))
        }
        let wall = (-8...8).map { (3.0, Double($0) * 0.05) }
        let trunk = try robustCircle(ring + wall, near: (0.08, 0), maxDistance: 0.5)
        assertClose(trunk.radius, 0.08, tol: 1e-3)
        assertClose(trunk.x, 0, tol: 1e-3)
        let circle = try fitCircle([(1.08, 2), (1, 2.08), (0.92, 2), (1, 1.92)])
        assertClose(circle.x, 1, tol: 1e-6)
        assertClose(circle.radius, 0.08, tol: 1e-6)
        let nudged = nudgeTowardCamera(hit: Vec3(0, 0, 2), camera: .zero, meters: 0.4)
        assertClose(nudged.z, 1.6)
    }

    func testFillFromBottom() throws {
        let pixels = try compile(fixture("straight-1m.json"))
        let dark = try effectColors(pixels, .fillBottom, phase: 0)
        XCTAssertEqual(dark[0], RGB(255, 244, 230))
        XCTAssertEqual(dark[10], RGB(0, 0, 0))
        let full = try effectColors(pixels, .fillBottom, phase: 1)
        XCTAssertTrue(full.allSatisfy { $0 == RGB(255, 244, 230) })
        let rainbow = try effectColors(pixels, .heightRainbow, phase: 0)
        XCTAssertNotEqual(rainbow[0], rainbow[pixels.count / 2])
    }

    func testDriftSpreadsByPathDistance() throws {
        let points = [Vec3(0, 0, 0), Vec3(1, 0, 0), Vec3(0, 0, 0)]
        let result = try distributeDrift(points, retap: Vec3(0, 0.4, 0))
        assertClose(result.errorM, 0.4)
        assertClose(result.weights[0], 0)
        assertClose(result.weights[1], 1 / 2.4)
        assertClose(result.weights[2], 2 / 2.4)
        XCTAssertEqual(result.corrected[0], points[0])
        assertClose(result.corrected[2].y, -0.4 * 2 / 2.4)
        let held = try distributeDrift([Vec3(5, 5, 5), Vec3(0, 0, 0), Vec3(1, 0, 0)], retap: Vec3(0, 0.3, 0), referenceIndex: 1)
        XCTAssertEqual(held.corrected[0], Vec3(5, 5, 5))
        assertClose(held.weights[0], 0)
    }

    func testTwoMarksYawAndThreeMarksKabsch() throws {
        let yaw = 0.4
        let shift = Vec3(2, -1, 3)
        let source = [Vec3(0, 0, 0), Vec3(1, 0.2, 0)]
        let target = source.map { applyRotation(yawMatrix(yaw), $0) + shift }
        let rigid = try alignSessions(source, target, referenceIds: ["base", "tip"])
        XCTAssertEqual(rigid.method, .yawTranslation)
        assertClose(rigid.yawRadians, yaw)
        assertClose(rigid.residualM, 0, tol: 1e-9)
        assertClose(rigid.rotation[4], 1)
        for index in source.indices {
            assertClose(distance(rigid.apply(source[index]), target[index]), 0, tol: 1e-9)
        }
        let pitched = source.map { rodrigues($0, Vec3(1, 0, 0), 20 * Double.pi / 180) }
        let flat = try alignSessions(source, pitched)
        XCTAssertEqual(flat.method, .yawTranslation)
        XCTAssertGreaterThan(flat.residualM, 1e-3)
        assertClose(flat.rotation[4], 1)

        let square = [Vec3(0, 0, 0), Vec3(1, 0, 0), Vec3(0, 0.2, 1), Vec3(1, 0.4, 1)]
        let tilted = square.map { rodrigues($0, Vec3(1, 2, 3), 0.6) + Vec3(1, -2, 0.5) }
        let full = try alignSessions(square, tilted)
        XCTAssertEqual(full.method, .kabsch)
        assertClose(full.residualM, 0, tol: 1e-6)

        let frame = FrameTransform(translation: Vec3(-1, 2, 0.5), yawRadians: 0.7)
        let point = Vec3(0.2, -3, 1.4)
        let same = rigidFromFrame(frame)
        assertClose(distance(same.apply(point), applyFrame(point, frame)), 0, tol: 1e-12)
    }

    func testBranchChaseAndSchemaVersionTwo() throws {
        let tree = try fixture("branching-tree.json")
        XCTAssertEqual(tree.schemaVersion, 1)
        XCTAssertEqual(tree.groups?.count, 3)
        XCTAssertEqual(tree.references?.count, 3)
        let pixels = try compile(tree)
        let branchOne = pixels.filter { $0.group == "branch-1" }
        let branchTwo = pixels.filter { $0.group == "branch-2" }
        let trunk = pixels.filter { $0.groupRole == "trunk" }
        assertClose(branchOne[0].branchU, 0)
        assertClose(branchOne[branchOne.count - 1].branchU, 1)
        assertClose(branchTwo[0].branchU, 1)
        assertClose(branchTwo[branchTwo.count - 1].branchU, 0)
        XCTAssertLessThan(trunk[0].branchU, trunk[trunk.count - 1].branchU)

        let leaving = try effectColors(pixels, .branchChase, phase: 0, address: EffectAddress(target: "branches"))
        let arriving = try effectColors(pixels, .branchChase, phase: 1, address: EffectAddress(target: "branches"))
        for index in pixels.indices {
            let pixel = pixels[index]
            if pixel.groupRole != "branch" {
                XCTAssertEqual(leaving[index], RGB(0, 0, 0))
                XCTAssertEqual(arriving[index], RGB(0, 0, 0))
            } else if abs(pixel.branchU) < 1e-9 {
                XCTAssertEqual(leaving[index], RGB(255, 244, 230))
                XCTAssertEqual(arriving[index], RGB(0, 0, 0))
            } else if abs(pixel.branchU - 1) < 1e-9 {
                XCTAssertEqual(leaving[index], RGB(0, 0, 0))
                XCTAssertEqual(arriving[index], RGB(255, 244, 230))
            }
        }
        let ring = try effectColors(pixels, .heightRainbow, phase: 0, address: EffectAddress(target: "all branches", heightM: 1.4, bandM: 0.05))
        let lit = zip(pixels, ring).filter { $0.1 != RGB(0, 0, 0) }.map(\.0)
        XCTAssertFalse(lit.isEmpty)
        XCTAssertTrue(lit.allSatisfy { $0.groupRole == "branch" && abs($0.y - 1.4) <= 0.05 + 1e-9 })
        XCTAssertThrowsError(try effectColors(pixels, .branchChase, phase: 0, address: EffectAddress(target: "nope")))

        let straight = try fixture("straight-1m.json")
        let marks = deriveReferences(straight.waypoints)
        XCTAssertEqual(marks.map(\.label), ["Start", "End"])
        var versionTwo = straight
        versionTwo.schemaVersion = 2
        versionTwo.references = marks
        versionTwo.alignments = [
            SessionAlignment(
                method: .yawTranslation,
                rotation: [1, 0, 0, 0, 1, 0, 0, 0, 1],
                translation: .zero,
                yawRadians: 0,
                residualM: 0.012,
                referenceIds: marks.map(\.id)
            )
        ]
        let encoded = try JSONEncoder().encode(versionTwo)
        let schema = try Data(contentsOf: repoRoot().appendingPathComponent("schema/fixture.v1.json"))
        let errors = try SchemaCheck.validateFixture(encoded, schemaData: schema)
        XCTAssertEqual(errors, [], errors.joined(separator: "\n"))
        let decoded = try JSONDecoder().decode(Fixture.self, from: encoded)
        XCTAssertEqual(decoded.schemaVersion, 2)
        XCTAssertEqual(decoded.alignments?.first?.method, .yawTranslation)
        XCTAssertEqual(SchemaCheck.schemaVersions, [1, 2])
    }

    private func rodrigues(_ point: Vec3, _ axis: Vec3, _ angle: Double) -> Vec3 {
        let unit = axis.normalized()
        return point * cos(angle) + unit.cross(point) * sin(angle) + unit * (unit.dot(point) * (1 - cos(angle)))
    }

    private func fixture(_ name: String) throws -> Fixture {
        let data = try Data(contentsOf: repoRoot().appendingPathComponent("fixtures/\(name)"))
        return try JSONDecoder().decode(Fixture.self, from: data)
    }

    private func repoRoot() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    }

    private func assertClose(_ value: Double, _ expected: Double, tol: Double = 1e-6, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(value, expected, accuracy: tol, file: file, line: line)
    }
}
