import MapperGeometry
import SwiftUI
import UniformTypeIdentifiers

private let importTypes: [UTType] = {
    var types: [UTType] = [.json, .xml, .commaSeparatedText, .plainText, .data]
    if let xmodel = UTType(filenameExtension: "xmodel") {
        types.append(xmodel)
    }
    return types
}()

struct FixtureListView: View {
    @EnvironmentObject private var model: AppModel
    @State private var creating = false
    @State private var importing = false
    @State private var name = ""
    @State private var hint: SurfaceHint = .freeform
    @State private var scaleFile: URL?
    @State private var askScale = false
    @State private var scaleText = "1"
    @State private var proposals: [ImportItem] = []

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            VStack(alignment: .leading, spacing: 18) {
                Text("WLED Mapper")
                    .font(.largeTitle.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                    .padding(.top, 8)

                if model.fixtures.isEmpty {
                    Spacer()
                    Text("No fixtures yet.")
                        .font(.title3)
                        .foregroundStyle(Theme.muted)
                    Text("Walk the strip and tap where the wire goes.")
                        .foregroundStyle(Theme.muted)
                    Spacer()
                } else {
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            ForEach(model.fixtures) { fixture in
                                NavigationLink(value: Route.fixture(fixture.id)) {
                                    row(fixture)
                                }
                                .buttonStyle(.plain)
                                .contextMenu {
                                    Button("Delete", role: .destructive) {
                                        Task { await model.delete(fixture.id) }
                                    }
                                }
                            }
                        }
                    }
                }

                if let drift = model.status.xlightsDrift, drift.inSync == false {
                    Text("xLights controllers differ from the fleet.")
                        .font(.subheadline)
                        .foregroundStyle(Theme.muted)
                }
                if let message = model.message {
                    Text(message)
                        .font(.callout)
                        .foregroundStyle(Theme.warn)
                }
                if model.status.playerConfigured == true {
                    BigButton(title: model.status.showRunning == true ? "Stop show" : "Start show") {
                        Task { await model.toggleShow() }
                    }
                    .accessibilityIdentifier("show.toggle")
                }
                BigButton(title: "Map a fixture") { creating = true }
                BigButton(title: "Import", prominent: false) { importing = true }
                    .accessibilityIdentifier("fixtures.import")
            }
            .padding(24)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("screen.fixtures")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .navigationBar)
        .sheet(isPresented: $creating) {
            newFixture
                .presentationDetents([.medium])
                .presentationDragIndicator(.visible)
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: importTypes, allowsMultipleSelection: false) { result in
            switch result {
            case .success(let urls):
                if let url = urls.first {
                    if url.lastPathComponent.lowercased().contains("rgbeffects") {
                        scaleText = "1"
                        scaleFile = url
                        askScale = true
                    } else {
                        Task { await importFile(url, scale: nil) }
                    }
                }
            case .failure(let error):
                model.message = error.localizedDescription
            }
        }
        .sheet(isPresented: $askScale) {
            scaleSheet
                .presentationDetents([.medium])
                .presentationDragIndicator(.visible)
        }
        .confirmationDialog(
            proposalTitle,
            isPresented: Binding(get: { !proposals.isEmpty }, set: { if !$0 { proposals = [] } }),
            titleVisibility: .visible
        ) {
            Button("Leave it", role: .cancel) { proposals = [] }
            if proposals.first?.proposed != nil {
                Button("Use the xLights model") {
                    let proposed = proposals.first?.proposed
                    proposals = []
                    if let proposed {
                        Task { await accept(proposed) }
                    }
                }
            }
        } message: {
            Text("The phone capture stays until you choose.")
        }
    }

    private var proposalTitle: String {
        let item = proposals.first
        let name = item?.name ?? "This fixture"
        if item?.phoneCapture == true {
            return "\(name) was mapped on the phone."
        }
        return "Use the xLights model for \(name)?"
    }

    private var scaleSheet: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            VStack(alignment: .leading, spacing: 18) {
                Text("Metres per xLights unit")
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                TextField("1", text: $scaleText)
                    .font(.title3)
                    .foregroundStyle(Theme.ink)
                    .keyboardType(.decimalPad)
                    .padding(16)
                    .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                BigButton(title: "Import") {
                    let chosen = Double(scaleText.replacingOccurrences(of: ",", with: "."))
                    let file = scaleFile
                    askScale = false
                    scaleFile = nil
                    if let file {
                        Task { await importFile(file, scale: chosen) }
                    }
                }
                Spacer()
            }
            .padding(24)
        }
    }

    private func importFile(_ url: URL, scale: Double?) async {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            let data = try Data(contentsOf: url)
            guard let text = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1) else {
                model.message = "That file is not text."
                Haptics.warning()
                return
            }
            let outcome = try await model.client.importFixture(
                name: url.deletingPathExtension().lastPathComponent,
                filename: url.lastPathComponent,
                text: text,
                scaleMetresPerUnit: scale
            )
            switch outcome {
            case .fixture(let saved):
                model.replace(saved)
                model.message = nil
                model.path.append(.fixture(saved.id))
            case .batch(let batch):
                var created: Fixture?
                var kept = 0
                var waiting: [ImportItem] = []
                for item in batch.results {
                    switch item.action {
                    case "created", "updated":
                        if let fixture = item.fixture {
                            model.replace(fixture)
                            if created == nil { created = fixture }
                        }
                    case "proposal":
                        waiting.append(item)
                        if item.phoneCapture == true { kept += 1 }
                    case "skipped":
                        break
                    default:
                        break
                    }
                }
                proposals = waiting
                if let created, waiting.isEmpty {
                    model.path.append(.fixture(created.id))
                }
                model.message = kept > 0 ? "Left \(kept) phone capture\(kept == 1 ? "" : "s") unchanged." : nil
            }
            Haptics.success()
        } catch {
            model.message = error.localizedDescription
            Haptics.warning()
        }
    }

    private func accept(_ fixture: Fixture) async {
        do {
            let saved = try await model.client.update(fixture)
            model.replace(saved)
            model.message = nil
            if proposals.isEmpty {
                model.path.append(.fixture(saved.id))
            }
        } catch {
            model.message = error.localizedDescription
        }
    }

    private func row(_ fixture: Fixture) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(fixture.name)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                Text(model.showRunning(fixture) ? "Show running" : "\(fixture.surfaceHint.title) · \(fixture.runs.count) output\(fixture.runs.count == 1 ? "" : "s")")
                    .font(.subheadline)
                    .foregroundStyle(Theme.muted)
            }
            Spacer()
            Image(systemName: "chevron.right")
                .foregroundStyle(Theme.muted)
        }
        .padding(18)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    private var newFixture: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            VStack(alignment: .leading, spacing: 18) {
                Text("New fixture")
                    .font(.title.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                TextField("Front tree", text: $name)
                    .font(.title3)
                    .foregroundStyle(Theme.ink)
                    .padding(16)
                    .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                Picker("Shape", selection: $hint) {
                    ForEach(SurfaceHint.allCases, id: \.self) { item in
                        Text(item.title).tag(item)
                    }
                }
                .pickerStyle(.segmented)
                BigButton(title: "Start") {
                    let chosen = name
                    let surface = hint
                    creating = false
                    Task {
                        if let id = await model.create(name: chosen, hint: surface) {
                            model.path.append(Route.capture(id))
                        }
                    }
                }
                Spacer()
            }
            .padding(24)
        }
    }
}
