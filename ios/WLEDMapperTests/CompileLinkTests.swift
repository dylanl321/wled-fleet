import MapperGeometry
import XCTest

final class CompileLinkTests: XCTestCase {
    func testStraightFixturePlacesSixtyPixels() throws {
        let bundle = Bundle(for: CompileLinkTests.self)
        let url = try XCTUnwrap(bundle.url(forResource: "straight-1m", withExtension: "json"))
        let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: url))
        let pixels = try compile(fixture)
        XCTAssertEqual(pixels.count, 60)
        XCTAssertEqual(pixels[30].y, 0.5, accuracy: 1e-6)
    }
}
