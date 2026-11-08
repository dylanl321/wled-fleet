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

                if let message = model.message {
                    Text(message)
                        .font(.callout)
                        .foregroundStyle(Theme.warn)
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
                    Task { await importFile(url) }
                }
            case .failure(let error):
                model.message = error.localizedDescription
            }
        }
    }

    private func importFile(_ url: URL) async {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            let data = try Data(contentsOf: url)
            guard let text = String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1) else {
                model.message = "That file is not text."
                Haptics.warning()
                return
            }
            let saved = try await model.client.importFixture(
                name: url.deletingPathExtension().lastPathComponent,
                filename: url.lastPathComponent,
                text: text
            )
            model.replace(saved)
            model.message = nil
            model.path.append(.fixture(saved.id))
            Haptics.success()
        } catch {
            model.message = error.localizedDescription
            Haptics.warning()
        }
    }

    private func row(_ fixture: Fixture) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(fixture.name)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                Text("\(fixture.surfaceHint.title) · \(fixture.runs.count) output\(fixture.runs.count == 1 ? "" : "s")")
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
