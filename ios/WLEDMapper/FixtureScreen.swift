import MapperGeometry
import SwiftUI
import UIKit

struct FixtureScreen: View {
    @EnvironmentObject private var model: AppModel
    var fixtureId: String
    var previewOnly = false

    @State private var pixels: [MappedPixel] = []
    @State private var phase = 0.2
    @State private var playing: EffectName?
    @State private var confirmLedmap = false
    @State private var confirmShare = false
    @State private var editing = false
    @State private var more = false
    @State private var started = Date()
    @State private var target = "all"
    @State private var limitHeight = false
    @State private var heightM = 1.4
    @State private var limitAlong = false
    @State private var along = 0.5
    @State private var pushAddress: Task<Void, Never>?

    private var fixture: Fixture? { model.fixture(fixtureId) }

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            VStack(spacing: 14) {
                header
                PixelCloudView(pixels: shownPixels, colors: colors)
                    .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                    .accessibilityIdentifier("screen.preview")
                if showsBranches { targetMenu }
                effects
                if !previewOnly {
                    BigButton(title: "Find every pixel") {
                        model.path.append(Route.find(fixtureId))
                    }
                    .accessibilityIdentifier("fixture.find")
                    HStack(spacing: 10) {
                        BigButton(title: "Capture", prominent: false) {
                            model.path.append(Route.capture(fixtureId))
                        }
                        BigButton(title: "Ledmap", prominent: false) {
                            if let fixture, model.showRunning(fixture) {
                                model.message = "Show running"
                            } else {
                                confirmLedmap = true
                            }
                        }
                        BigButton(title: "Share", prominent: false) { confirmShare = true }
                    }
                }
            }
            .padding(16)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(previewOnly ? "screen.preview" : "screen.fixture")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .navigationBar)
        .task(id: fixture?.id) { await load() }
        .task(id: playing) { await animate() }
        .onChange(of: target) { _, _ in schedulePush() }
        .onChange(of: limitHeight) { _, _ in schedulePush() }
        .onChange(of: heightM) { _, _ in schedulePush() }
        .onChange(of: limitAlong) { _, _ in schedulePush() }
        .onChange(of: along) { _, _ in schedulePush() }
        .confirmationDialog("Send a ledmap? The controller reboots.", isPresented: $confirmLedmap, titleVisibility: .visible) {
            Button("By height") { Task { await push("height") } }
            Button("2D grid") { Task { await push("grid") } }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog("Share the map", isPresented: $confirmShare, titleVisibility: .visible) {
            Button("CSV") { Task { await share("csv") } }
            Button("JSON") { Task { await share("json") } }
            Button("Pixelblaze") { Task { await share("pixelblaze") } }
            Button("xLights grid") { Task { await share("xmodel") } }
            Button("xLights line") { Task { await share("polyline") } }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(isPresented: Binding(get: { model.shareURL != nil }, set: { if !$0 { model.shareURL = nil } })) {
            if let url = model.shareURL {
                ShareSheet(url: url)
            }
        }
        .sheet(isPresented: $more) { moreSheet }
        .sheet(isPresented: $editing) {
            if let fixture {
                EditFixtureSheet(fixture: fixture) { updated in
                    model.replace(updated)
                    if let compiled = try? compile(updated) {
                        pixels = compiled
                    }
                }
                .environmentObject(model)
            }
        }
    }

    private var header: some View {
        HStack {
            if !previewOnly && !model.path.isEmpty {
                Button {
                    model.path.removeLast()
                } label: {
                    Image(systemName: "chevron.left")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(Theme.ink)
                        .frame(width: 44, height: 44)
                        .background(Theme.card, in: Circle())
                }
                .buttonStyle(.plain)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(fixture?.name ?? "Fixture")
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                if let fixture, model.showRunning(fixture) {
                    Text("Show running")
                        .font(.subheadline)
                        .foregroundStyle(Theme.muted)
                } else if let message = model.message, !previewOnly {
                    Text(message)
                        .font(.subheadline)
                        .foregroundStyle(Theme.warn)
                        .lineLimit(3)
                } else {
                    Text("\(shownPixels.count) pixels")
                        .font(.subheadline)
                        .foregroundStyle(Theme.muted)
                }
            }
            Spacer()
            if !previewOnly {
                Button("Edit") { editing = true }
                    .font(.body.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                    .accessibilityIdentifier("fixture.edit")
            }
            Button {
                more = true
            } label: {
                Image(systemName: "slider.horizontal.3")
                    .font(.title3)
                    .foregroundStyle(Theme.ink)
                    .frame(width: 44, height: 44)
                    .background(Theme.card, in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("fixture.more")
        }
    }

    private var showsBranches: Bool {
        fixture?.surfaceHint == .branching || fixture?.groups?.contains { $0.role == .branch } == true
    }

    private var address: EffectAddress {
        EffectAddress(
            target: target,
            heightM: limitHeight ? heightM : nil,
            branchU: limitAlong ? along : nil,
            bandM: limitHeight ? 0.05 : nil,
            bandU: limitAlong ? 0.05 : nil
        )
    }

    private var targetTitle: String {
        switch target {
        case "all": return "All"
        case "branches": return "Branches"
        case "trunk": return "Trunk"
        default:
            return fixture?.groups?.first { $0.id == target }?.name ?? "All"
        }
    }

    private var targetMenu: some View {
        Menu {
            Button("All") { target = "all" }
            Button("Branches") { target = "branches" }
            ForEach(fixture?.groups ?? []) { group in
                Button(group.name) { target = group.id }
            }
        } label: {
            Text(targetTitle)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.ink)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(Theme.card, in: Capsule())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityIdentifier("effect.target")
    }

    private var effectNames: [EffectName] {
        showsBranches ? [.fillBottom, .heightRainbow, .branchChase] : [.fillBottom, .heightRainbow, .angleSpin]
    }

    private func shortTitle(_ name: EffectName) -> String {
        switch name {
        case .fillBottom: return "Fill"
        case .heightRainbow: return "Rainbow"
        case .angleSpin: return "Spin"
        case .branchChase: return "Chase"
        }
    }

    private var effects: some View {
        HStack(spacing: 8) {
            ForEach(effectNames, id: \.self) { name in
                effectButton(name, shortTitle(name))
            }
            Button {
                Haptics.undo()
                playing = nil
                Task { try? await model.client.stop(fixture: fixtureId) }
            } label: {
                Image(systemName: "stop.fill")
                    .frame(width: 52, height: 52)
                    .foregroundStyle(Theme.ink)
                    .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("effect.stop")
        }
    }

    private func start(_ name: EffectName) {
        Haptics.place()
        playing = name
        started = Date()
        phase = 0
        Task { try? await model.client.effect(fixture: fixtureId, name: name.rawValue, address: address) }
    }

    private func schedulePush() {
        guard playing != nil else { return }
        pushAddress?.cancel()
        let address = self.address
        let name = playing
        pushAddress = Task {
            try? await Task.sleep(nanoseconds: 200_000_000)
            guard !Task.isCancelled, let name else { return }
            try? await model.client.effect(fixture: fixtureId, name: name.rawValue, address: address)
        }
    }

    private func effectButton(_ name: EffectName, _ title: String) -> some View {
        Button {
            start(name)
        } label: {
            Text(title)
                .font(.body.weight(.semibold))
                .frame(maxWidth: .infinity)
                .frame(height: 52)
                .foregroundStyle(playing == name ? Color.black : Theme.ink)
                .background(playing == name ? Theme.amber : Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("effect.\(name.rawValue)")
    }

    private var shownPixels: [MappedPixel] {
        if !pixels.isEmpty { return pixels }
        if let fixture, let local = try? compile(fixture) {
            return local
        }
        return pixels
    }

    private var colors: [RGB] {
        let name = playing ?? .heightRainbow
        let shown = playing == nil ? 0.22 : phase
        return (try? effectColors(shownPixels, name, phase: shown, address: address)) ?? []
    }

    private func load() async {
        if previewOnly || model.screenshot != nil, let fixture, let local = try? compile(fixture) {
            pixels = local
            return
        }
        if let remote = try? await model.client.pixelmap(id: fixtureId), !remote.isEmpty {
            pixels = remote
            return
        }
        if let fixture, let local = try? compile(fixture) {
            pixels = local
        }
    }

    private func animate() async {
        guard playing != nil else { return }
        while !Task.isCancelled && playing != nil {
            phase = Date().timeIntervalSince(started) / 6
            try? await Task.sleep(nanoseconds: 66_000_000)
        }
    }

    private func scalePitches() async {
        guard let fixture else { return }
        do {
            var latest = fixture
            for run in fixture.runs {
                latest = try await model.client.scalePitch(fixture: fixture.id, run: run.id)
            }
            model.replace(latest)
            if let local = try? compile(latest) { pixels = local }
            Haptics.success()
        } catch {
            model.message = error.localizedDescription
            Haptics.warning()
        }
    }

    private func push(_ mode: String) async {
        do {
            try await model.client.ledmap(fixture: fixtureId, slot: model.ledmapSlot, mode: mode, width: model.gridWidth, height: model.gridHeight)
            Haptics.success()
            model.message = nil
        } catch {
            model.message = error.localizedDescription
            Haptics.warning()
        }
    }

    private func share(_ format: String) async {
        do {
            model.shareURL = try await model.client.export(
                fixture: fixtureId,
                format: format,
                width: model.gridWidth,
                height: model.gridHeight,
                depth: model.gridDepth
            )
            model.message = nil
        } catch {
            model.message = error.localizedDescription
        }
    }

    private var moreSheet: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            VStack(alignment: .leading, spacing: 16) {
                Text("Ledmap")
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                Stepper("Slot \(model.ledmapSlot)", value: $model.ledmapSlot, in: 1...9)
                    .foregroundStyle(Theme.ink)
                Stepper("Grid width \(model.gridWidth)", value: $model.gridWidth, in: 1...128)
                    .foregroundStyle(Theme.ink)
                Stepper("Grid height \(model.gridHeight)", value: $model.gridHeight, in: 1...128)
                Stepper("Grid depth \(model.gridDepth)", value: $model.gridDepth, in: 1...32)
                    .foregroundStyle(Theme.ink)
                Button("Scale pitch to the controller count") {
                    Task { await scalePitches() }
                }
                .font(.body.weight(.semibold))
                .foregroundStyle(Theme.ink)
                Button("Find pixels with the camera") {
                    more = false
                    model.path.append(.find(fixtureId))
                }
                .font(.body.weight(.semibold))
                .foregroundStyle(Theme.ink)
                Text("A ledmap is only for a fixture on one controller. Upload reboots that board. Scaling the pitch, or setting a span's pixel count while capturing, fixes a count that does not match the board.")
                    .font(.callout)
                    .foregroundStyle(Theme.muted)
                if showsBranches {
                    Text("Where it plays")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(Theme.ink)
                    Toggle("At a height", isOn: $limitHeight)
                        .foregroundStyle(Theme.ink)
                    if limitHeight {
                        Slider(value: $heightM, in: 0...max(heightM, 3))
                            .tint(Theme.amber)
                        Text(String(format: "%.2f m", heightM))
                            .foregroundStyle(Theme.muted)
                    }
                    Toggle("Along the branch", isOn: $limitAlong)
                        .foregroundStyle(Theme.ink)
                    if limitAlong {
                        Slider(value: $along, in: 0...1)
                            .tint(Theme.amber)
                        Text(along < 0.05 ? "At the trunk" : String(format: "%.0f%% along", along * 100))
                            .foregroundStyle(Theme.muted)
                    }
                    if !effectNames.contains(.angleSpin) {
                        Button("Angle spin") {
                            more = false
                            start(.angleSpin)
                        }
                        .font(.body.weight(.semibold))
                        .foregroundStyle(Theme.ink)
                    }
                }
                if let message = model.message {
                    Text(message).foregroundStyle(Theme.warn)
                }
                Spacer()
            }
            .padding(24)
        }
        .presentationDetents([.medium, .large])
    }
}

struct ShareSheet: UIViewControllerRepresentable {
    var url: URL

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
