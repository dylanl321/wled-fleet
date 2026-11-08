import XCTest

final class ScreenshotTests: XCTestCase {
    override func setUp() {
        super.setUp()
        continueAfterFailure = false
    }

    func testConnectScreen() {
        capture("connect")
    }

    func testFixtureList() {
        capture("fixtures")
    }

    func testTreePreview() {
        capture("preview")
    }

    private func capture(_ name: String) {
        let app = XCUIApplication()
        app.launchArguments = ["-mapperScreenshot", name]
        app.launch()
        let marker: XCUIElement
        switch name {
        case "connect":
            marker = app.staticTexts["Point the phone at the lights."]
        case "fixtures":
            marker = app.staticTexts["Straight 1m"]
        default:
            marker = app.staticTexts["Front tree"]
        }
        XCTAssertTrue(marker.waitForExistence(timeout: 20), "\(name) did not appear")
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
