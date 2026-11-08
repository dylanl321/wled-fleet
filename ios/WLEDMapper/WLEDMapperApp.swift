import MapperGeometry
import SwiftUI

@main
struct WLEDMapperApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .preferredColorScheme(.dark)
                .tint(Theme.amber)
        }
    }
}

enum Route: Hashable {
    case fixture(String)
    case capture(String)
    case numbers(String)
    case detect(String)
}

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationStack(path: $model.path) {
            start
                .navigationDestination(for: Route.self) { route in
                    switch route {
                    case .fixture(let id):
                        FixtureScreen(fixtureId: id)
                    case .capture(let id):
                        CaptureScreen(fixtureId: id)
                    case .numbers(let id):
                        if let fixture = model.fixture(id) {
                            EditFixtureSheet(fixture: fixture) { updated in
                                model.replace(updated)
                            }
                        }
                    case .detect(let id):
                        DetectScreen(fixtureId: id)
                    }
                }
        }
        .task { await model.restore() }
    }

    @ViewBuilder
    private var start: some View {
        switch model.screenshot {
        case .connect:
            ConnectView()
        case .fixtures:
            FixtureListView()
        case .preview:
            FixtureScreen(fixtureId: AppModel.treeFixtureId, previewOnly: true)
        case .numbers:
            if let fixture = model.fixture(AppModel.straightFixtureId) {
                EditFixtureSheet(fixture: fixture) { _ in }
            }
        case .detect:
            DetectScreen(fixtureId: AppModel.straightFixtureId, previewOnly: true)
        case nil:
            if model.connected || !model.fixtures.isEmpty {
                FixtureListView()
            } else {
                ConnectView()
            }
        }
    }
}
