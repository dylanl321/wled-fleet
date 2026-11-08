import Foundation
import XCTest
@testable import MapperGeometry

final class DetectionTests: XCTestCase {
    func testBinaryPlanUsesReferenceFramesAndBitGroups() {
        let eight = codeFrames(indices: Array(0..<8), mode: .binary)
        XCTAssertEqual(eight.count, 5)
        XCTAssertEqual(eight[0].role, .off)
        XCTAssertEqual(eight[1].role, .on)
        XCTAssertEqual(eight[1].lit, Array(0..<8))
        XCTAssertEqual(eight[2].bit, 0)
        XCTAssertEqual(eight[2].lit, [1, 3, 5, 7])
        XCTAssertEqual(eight[4].lit, [4, 5, 6, 7])
        XCTAssertEqual(codeFrames(indices: Array(0..<500), mode: .binary).count, 11)
        XCTAssertEqual(codeFrames(indices: [4], mode: .binary).count, 2)
        let oneByOne = codeFrames(indices: [10, 11, 12], mode: .sequential)
        XCTAssertEqual(oneByOne.count, 5)
        XCTAssertEqual(oneByOne[2].role, .single)
        XCTAssertEqual(oneByOne[2].lit, [10])
    }

    func testBrightBlobsAndDarknessOnSyntheticFrames() {
        var image = [UInt8](repeating: 0, count: 32 * 24)
        paint(&image, width: 32, x: 8, y: 6, value: 255)
        paint(&image, width: 32, x: 20, y: 14, value: 220)
        let blobs = detectBrightBlobs(luminance: image, width: 32, height: 24, threshold: 40)
        XCTAssertEqual(blobs.count, 2)
        XCTAssertEqual(blobs[0].centroid.x, 9, accuracy: 0.2)
        XCTAssertEqual(blobs[0].centroid.y, 7, accuracy: 0.2)
        XCTAssertEqual(blobs[1].centroid.x, 21, accuracy: 0.2)

        let reduced = downsampleMax(image, width: 32, height: 24, factor: 2)
        XCTAssertEqual(reduced.width, 16)
        let reducedBlobs = detectBrightBlobs(luminance: reduced.bytes, width: reduced.width, height: reduced.height, threshold: 40)
        let lifted = fullResolution(reducedBlobs[0].centroid, scale: 2)
        XCTAssertEqual(lifted.x, blobs[0].centroid.x, accuracy: 1.5)

        XCTAssertTrue(roomBrightness([UInt8](repeating: 8, count: 50)).dark)
        var mixed = [UInt8](repeating: 5, count: 88)
        mixed.append(contentsOf: [UInt8](repeating: 250, count: 12))
        XCTAssertFalse(roomBrightness(mixed).dark)
        XCTAssertEqual(thresholdFromReference(off: [UInt8](repeating: 4, count: 8), on: [4, 4, 200]), 102)
    }

    func testDecodeBinaryCodesAndRejectsAStaticLight() {
        let indices = [10, 11, 12, 13, 14]
        let frames = codeFrames(indices: indices, mode: .binary)
        let spots = (0..<5).map { ImagePoint(x: Double($0) * 20 + 5, y: 12) }
        var observations = frames.map { frame -> [ImagePoint] in
            if frame.role == .off { return [ImagePoint(x: 90, y: 40)] }
            if frame.role == .on { return spots + [ImagePoint(x: 90, y: 40)] }
            return frame.lit.compactMap { index in
                indices.firstIndex(of: index).map { spots[$0] }
            }
        }
        let decoded = decodeBlobs(frames: frames, observations: observations, matchRadius: 3)
        let found = Set(decoded.compactMap(\.index))
        XCTAssertEqual(found, Set(indices))
        XCTAssertFalse(decoded.contains { $0.centroid.x == 90 && $0.index != nil })

        observations[1].removeAll { $0.x == 45 }
        let missing = decodeBlobs(frames: frames, observations: observations, matchRadius: 3)
        XCTAssertFalse(missing.contains { $0.index == 12 })
        let alone = codeFrames(indices: [7], mode: .binary)
        let seen = decodeBlobs(frames: alone, observations: [[], [ImagePoint(x: 4, y: 4)]], matchRadius: 2)
        XCTAssertEqual(seen.first?.index, 7)
    }

    func testRayRoundTripTriangulationAndSegmentRefine() {
        let intrinsics = CameraIntrinsics(fx: 200, fy: 200, cx: 100, cy: 80)
        var pose = CameraPose.identity
        pose.origin = Vec3(0.1, 0, 1)
        let world = Vec3(0.1, 0.2, 0)
        let relative = world - pose.origin
        let u = intrinsics.cx + intrinsics.fx * relative.x / -relative.z
        let v = intrinsics.cy + intrinsics.fy * relative.y / relative.z
        let ray = rayFromPixel(u: u, v: v, intrinsics: intrinsics, pose: pose)
        let along = (world - ray.origin).dot(ray.direction)
        let closest = ray.origin + ray.direction * along
        XCTAssertEqual(distance(closest, world), 0, accuracy: 1e-6)
        XCTAssertGreaterThan(along, 0)

        let other = Ray(origin: Vec3(0.1, 1, 1), direction: (world - Vec3(0.1, 1, 1)).normalized())
        let hit = triangulate(ray, other)
        XCTAssertEqual(hit?.gap ?? 1, 0, accuracy: 1e-6)
        XCTAssertEqual(distance(hit?.position ?? Vec3(9, 9, 9), world), 0, accuracy: 1e-6)

        let grazing = Ray(origin: Vec3(0, 0.5, 1), direction: Vec3(0, 0, -1))
        let segment = closestOnSegment(grazing, Vec3(-1, 0, 0), Vec3(1, 0, 0))
        XCTAssertEqual(segment?.point.x ?? 9, 0, accuracy: 1e-6)
        XCTAssertEqual(segment?.point.y ?? 9, 0, accuracy: 1e-6)
        XCTAssertEqual(segment?.distance ?? 9, 0.5, accuracy: 1e-6)

        let model = [
            ModelSample(index: 0, position: Vec3(0, 0, 0), segment: "span"),
            ModelSample(index: 1, position: Vec3(0.2, 0, 0), segment: "span"),
        ]
        let looking = Ray(origin: Vec3(0.1, 0.02, 1), direction: Vec3(0, 0, -1))
        let refined = refineOnModel(looking, model, maxDistance: 0.25)
        XCTAssertEqual(refined?.position.x ?? 9, 0.1, accuracy: 1e-6)
        XCTAssertEqual(refined?.position.y ?? 9, 0, accuracy: 1e-6)
    }

    func testAssessOcclusionOutliersAndAcceptance() throws {
        let model = [
            ModelSample(index: 0, position: Vec3(0, 0, 0), segment: "span"),
            ModelSample(index: 1, position: Vec3(1, 0, 0), segment: "span"),
        ]
        let seen = Sighting(index: 0, ray: Ray(origin: Vec3(0, 0.01, 1), direction: Vec3(0, 0, -1)))
        let open = assessPixels(model: model, viewpoints: [[seen]], finished: false)
        XCTAssertEqual(open[0].method, .segment)
        XCTAssertFalse(open[0].outlier)
        XCTAssertTrue(open[1].occluded)
        XCTAssertFalse(open[1].missing)
        let done = assessPixels(model: model, viewpoints: [[seen]], finished: true)
        XCTAssertTrue(done[1].missing)
        XCTAssertFalse(done[1].occluded)

        let far = triangulate(
            Ray(origin: Vec3(-1, 2, 1), direction: Vec3(0, 0, -1)),
            Ray(origin: Vec3(1, 2, 1), direction: Vec3(0, 0, -1))
        )
        XCTAssertNil(far)
        let above = Vec3(0, 1, 0)
        let pair = [
            Sighting(index: 0, ray: Ray(origin: Vec3(-1, 1, 1), direction: (above - Vec3(-1, 1, 1)).normalized())),
            Sighting(index: 0, ray: Ray(origin: Vec3(1, 1, 1), direction: (above - Vec3(1, 1, 1)).normalized())),
        ]
        let odd = assessPixels(model: model, viewpoints: [pair], finished: true)
        XCTAssertEqual(odd[0].method, .triangulated)
        XCTAssertTrue(odd[0].outlier)
        XCTAssertEqual(odd[0].position?.y ?? 0, 1, accuracy: 1e-6)

        var fixture = try load("straight-1m.json")
        let moved = PixelAssessment(
            index: 0, position: Vec3(0, 2, 0), confidence: 0.8, method: .segment,
            viewpoints: 1, outlier: false, missing: false, occluded: false, segment: "seg-straight"
        )
        let dead = PixelAssessment(
            index: 1, position: nil, confidence: 0, method: nil,
            viewpoints: 0, outlier: false, missing: true, occluded: false, segment: "seg-straight"
        )
        fixture = applyingCorrections(fixture, runId: "run-straight", assessments: [moved, dead], includeOutliers: false)
        XCTAssertEqual(fixture.schemaVersion, 2)
        XCTAssertEqual(fixture.pixelCorrections.count, 1)
        let pixels = try compile(fixture)
        XCTAssertEqual(pixels[0].y, 2, accuracy: 1e-9)
        XCTAssertNotEqual(pixels[1].y, 2, accuracy: 1e-6)

        let path = applyingDetectedPath(
            try load("straight-1m.json"),
            runId: "run-straight",
            assessments: [
                moved,
                PixelAssessment(
                    index: 59, position: Vec3(0, 1.2, 0), confidence: 0.7, method: .triangulated,
                    viewpoints: 2, outlier: false, missing: false, occluded: false, segment: "seg-straight"
                ),
            ],
            includeOutliers: false
        )
        XCTAssertTrue(path.waypoints.contains { $0.source == .detected })
        XCTAssertEqual(path.runs[0].segments.count, 1)
        XCTAssertEqual(path.runs[0].segments[0].kind, .polyline)
        let data = try JSONEncoder().encode(path)
        let schema = try Data(contentsOf: repoRoot().appendingPathComponent("schema/fixture.v1.json"))
        let errors = try SchemaCheck.validateFixture(data, schemaData: schema)
        XCTAssertEqual(errors, [], errors.joined(separator: "; "))
        let original = try Data(contentsOf: repoRoot().appendingPathComponent("fixtures/straight-1m.json"))
        XCTAssertEqual(try SchemaCheck.validateFixture(original, schemaData: schema), [])
    }

    private func paint(_ image: inout [UInt8], width: Int, x: Int, y: Int, value: UInt8) {
        for dy in 0..<3 {
            for dx in 0..<3 {
                image[(y + dy) * width + (x + dx)] = value
            }
        }
    }

    private func load(_ name: String) throws -> Fixture {
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
}
