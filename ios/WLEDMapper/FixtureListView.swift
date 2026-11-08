import MapperGeometry
import SwiftUI
import UniformTypeIdentifiers

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

                BigButton(title: "Map a fixture") { creating = true }
                Button("Import a map") { importing = true }
                    .font(.body.weight(.medium))
                    .foregroundStyle(Theme.muted)
                    .frame(maxWidth: .infinity)
            }
            .padding(24)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("screen.fixtures")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .navigationBar)
        .fileImporter(isPresented: $importing, allowedContentTypes: [.commaSeparatedText, .json, .xml, .plainText]) { result in
            Task { await importFile(result) }
        }
        .sheet(isPresented: $creating) {
            newFixture
                .presentationDetents([.medium])
                .presentationDragIndicator(.visible)
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

    private func importFile(_ result: Result<URL, Error>) async {
        do {
            let url = try result.get()
            let access = url.startAccessingSecurityScopedResource()
            defer { if access { url.stopAccessingSecurityScopedResource() } }
            let text = try String(contentsOf: url, encoding: .utf8)
            let format: String
            switch url.pathExtension.lowercased() {
            case "csv": format = "csv"
            case "xmodel", "xml": format = "xmodel"
            default: format = "pixelblaze"
            }
            let saved = try await model.client.importMap(format: format, name: url.deletingPathExtension().lastPathComponent, text: text)
            model.replace(saved)
            Haptics.success()
            model.path.append(.fixture(saved.id))
        } catch {
            model.message = error.localizedDescription
            Haptics.warning()
        }
    }
}
