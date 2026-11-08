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
        let trunk = treePixels.filter { $0.controller == "d1:67:00:00:00:07" }
        let branches = treePixels.filter { $0.controller == "d1:67:00:00:00:08" }
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

    func testSchemaAcceptsFixturesAndRejectsATypo() throws {
        let schema = try Data(contentsOf: repoRoot().appendingPathComponent("schema/fixture.v1.json"))
        for name in ["straight-1m.json", "wall-outline.json", "branching-tree.json"] {
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
        let line = try fitHelix((0..<4).map { Vec3(0.2, Double($0) * 0.4, 0) })
        assertClose(line.radiusStart, 0, tol: 1e-6)
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

        let circle = try fitCircle([(1.08, 2), (1, 2.08), (0.92, 2), (1, 1.92)])
        assertClose(circle.x, 1, tol: 1e-6)
        assertClose(circle.radius, 0.08, tol: 1e-6)
        let nudged = nudgeTowardCamera(hit: Vec3(0, 0, 2), camera: .zero, meters: 0.4)
        assertClose(nudged.z, 1.6)
    }

    func testFillFromBottom() throws {
        let pixels = try compile(fixture("straight-1m.json"))
        let dark = effectColors(pixels, .fillBottom, phase: 0)
        XCTAssertEqual(dark[0], RGB(255, 244, 230))
        XCTAssertEqual(dark[10], RGB(0, 0, 0))
        let full = effectColors(pixels, .fillBottom, phase: 1)
        XCTAssertTrue(full.allSatisfy { $0 == RGB(255, 244, 230) })
        let rainbow = effectColors(pixels, .heightRainbow, phase: 0)
        XCTAssertNotEqual(rainbow[0], rainbow[pixels.count / 2])
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
