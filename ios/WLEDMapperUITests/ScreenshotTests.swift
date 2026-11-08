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
        let marker: String
        switch name {
        case "connect":
            marker = "Continue"
        case "fixtures":
            marker = "Straight 1m"
        default:
            marker = "Front tree"
        }
        XCTAssertTrue(app.staticTexts[marker].waitForExistence(timeout: 20), "\(name) did not show \(marker)")
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
