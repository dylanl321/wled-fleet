import MapperGeometry
import SwiftUI
import UIKit

struct FindScreen: View {
    @EnvironmentObject private var model: AppModel
    var fixtureId: String
    @StateObject private var find = FindModel()
    @StateObject private var camera = DetectCamera()
    @State private var details = false

    private var fixture: Fixture? { model.fixture(fixtureId) }

    var body: some View {
        ZStack {
            if find.phase == .preview {
                Theme.background.ignoresSafeArea()
            } else {
                FindCanvas(camera: camera).ignoresSafeArea()
                LinearGradient(colors: [.clear, .black.opacity(0.82)], startPoint: .center, endPoint: .bottom)
                    .ignoresSafeArea()
                    .allowsHitTesting(false)
            }
            VStack(spacing: 14) {
                header
                if find.phase == .preview {
                    preview
                } else {
                    Spacer(minLength: 0)
                    controls
                }
            }
            .padding(16)
        }
        .accessibilityIdentifier("screen.find")
        .toolbar(.hidden, for: .navigationBar)
        .onAppear { find.prepare(fixture) }
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        .onDisappear {
            UIApplication.shared.isIdleTimerDisabled = false
            Task { await find.abandon(model.client) }
        }
        .task(id: find.phase) {
            guard find.phase == .ready else { return }
            await find.watchRoom(camera)
        }
        .sheet(isPresented: $details) { detailsSheet }
    }

    private var header: some View {
        HStack {
            Button {
                Task { await leave() }
            } label: {
                Image(systemName: "chevron.left")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                    .frame(width: 44, height: 44)
                    .background(.black.opacity(0.45), in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("find.back")
            Spacer()
            if find.phase == .ready {
                Button {
                    details = true
                } label: {
                    Image(systemName: "slider.horizontal.3")
                        .font(.title3)
                        .foregroundStyle(Theme.ink)
                        .frame(width: 44, height: 44)
                        .background(.black.opacity(0.45), in: Circle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("find.details")
            }
        }
    }

    @ViewBuilder
    private var controls: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.title2.weight(.semibold))
                .foregroundStyle(Theme.ink)
            Text(subtitle)
                .font(.body)
                .foregroundStyle(Theme.ink.opacity(0.92))
                .fixedSize(horizontal: false, vertical: true)
            if let message = find.message {
                Text(message)
                    .font(.callout)
                    .foregroundStyle(Theme.warn)
            }
            if find.phase == .ready, let fixture, fixture.runs.count > 1 {
                Menu {
                    ForEach(fixture.runs) { run in
                        Button(runTitle(run)) { find.runId = run.id }
                    }
                } label: {
                    Text(selectedRunTitle)
                        .font(.body.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .frame(height: 48)
                        .foregroundStyle(Theme.ink)
                        .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                }
            }
            buttons
        }
        .padding(16)
        .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 22, style: .continuous))
    }

    @ViewBuilder
    private var buttons: some View {
        switch find.phase {
        case .ready:
            BigButton(title: startTitle) {
                Haptics.place()
                Task { await runFind(another: false) }
            }
            .disabled(!find.canStart)
            .opacity(find.canStart ? 1 : 0.45)
            .accessibilityIdentifier("find.start")
            if find.roomDark == false {
                Button("It's dark enough") {
                    find.trustTheRoom = true
                    find.message = nil
                }
                .font(.callout.weight(.medium))
                .foregroundStyle(Theme.muted)
                .frame(maxWidth: .infinity)
            }
        case .running:
            BigButton(title: "Cancel", prominent: false) {
                Task { await find.cancel(model.client) }
            }
        case .hidden:
            BigButton(title: "Other side") {
                Haptics.place()
                Task { await runFind(another: true) }
            }
            .accessibilityIdentifier("find.other")
            BigButton(title: "Use what I see", prominent: false) {
                find.finishWithWhatWeHave()
            }
        case .preview:
            EmptyView()
        }
    }

    private var preview: some View {
        let shown = find.previewCloud()
        return VStack(alignment: .leading, spacing: 12) {
            Text("Check the fit")
                .font(.title2.weight(.semibold))
                .foregroundStyle(Theme.ink)
            Text(find.summary)
                .font(.body)
                .foregroundStyle(Theme.muted)
            PixelCloudView(pixels: shown.pixels, colors: shown.colors)
                .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
            Text("Grey is the path you tapped. Amber is the camera. Red never lit, or landed far away.")
                .font(.caption)
                .foregroundStyle(Theme.muted)
            if let message = find.message {
                Text(message).font(.callout).foregroundStyle(Theme.warn)
            }
            BigButton(title: find.saving ? "Saving" : "Keep these") {
                Task { await keep(path: false) }
            }
            .disabled(!find.canKeep)
            .opacity(find.canKeep ? 1 : 0.45)
            .accessibilityIdentifier("find.keep")
            BigButton(title: "Replace the path", prominent: false) {
                Task { await keep(path: true) }
            }
            .disabled(!find.canReplace || find.saving)
            .opacity(find.canReplace ? 1 : 0.45)
            Spacer(minLength: 0)
        }
    }

    private var detailsSheet: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            VStack(alignment: .leading, spacing: 18) {
                Text("Details")
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                Text(find.mode == .binary ? "Groups light many pixels at once. It takes a handful of pictures." : "One by one lights a single pixel each picture. It is slower.")
                    .font(.callout)
                    .foregroundStyle(Theme.muted)
                HStack(spacing: 8) {
                    modeButton(.binary, "Groups")
                    modeButton(.sequential, "One by one")
                }
                Toggle("Include lights that landed far away", isOn: $find.includeOutliers)
                    .tint(Theme.amber)
                    .foregroundStyle(Theme.ink)
                Toggle("Use Vision contours", isOn: $find.useVision)
                    .tint(Theme.amber)
                    .foregroundStyle(Theme.ink)
                Spacer()
            }
            .padding(24)
        }
        .presentationDetents([.medium])
    }

    private func modeButton(_ mode: DetectMode, _ title: String) -> some View {
        Button {
            find.mode = mode
        } label: {
            Text(title)
                .font(.body.weight(.semibold))
                .frame(maxWidth: .infinity)
                .frame(height: 48)
                .foregroundStyle(find.mode == mode ? Color.black : Theme.ink)
                .background(find.mode == mode ? Theme.amber : Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    private var title: String {
        switch find.phase {
        case .ready: return "Find every pixel"
        case .running: return "Frame \(find.frameNumber) of \(find.frameCount)"
        case .hidden: return "Some were hidden"
        case .preview: return "Check the fit"
        }
    }

    private var subtitle: String {
        switch find.phase {
        case .ready:
            if find.roomDark == false { return "Turn the room lights off. The strip should be the only bright thing, and it is about to go dark." }
            if find.roomDark == true { return "Dark enough. Stand where you can see this strip, then hold still." }
            return "Stand where you can see this strip. It needs to be dark."
        case .running:
            return find.blobCount == 1 ? "Hold still. 1 light in this picture." : "Hold still. \(find.blobCount) lights in this picture."
        case .hidden:
            return "\(find.hiddenCount) \(find.hiddenCount == 1 ? "light was" : "lights were") behind something. Walk to the other side."
        case .preview:
            return find.summary
        }
    }

    private var startTitle: String {
        if find.busy { return "Starting" }
        if find.roomDark == false && !find.trustTheRoom { return "Too bright" }
        if find.roomDark == nil && !find.trustTheRoom { return "Waiting for the camera" }
        return "Start"
    }

    private var selectedRunTitle: String {
        guard let fixture, let run = fixture.runs.first(where: { $0.id == find.runId }) ?? fixture.runs.first else {
            return "Choose a strip"
        }
        return runTitle(run)
    }

    private func runTitle(_ run: Run) -> String {
        let name = model.controllers.first { $0.id == run.controllerId }?.name
        let who = name ?? "Output \(run.outputIndex + 1)"
        return "\(who) · \(run.pixelCount) pixels"
    }

    private func runFind(another: Bool) async {
        guard let fixture, let run = fixture.runs.first(where: { $0.id == find.runId }) ?? fixture.runs.first else { return }
        await find.capture(
            client: model.client,
            fixture: fixture,
            run: run,
            camera: camera,
            another: another
        )
    }

    private func keep(path: Bool) async {
        guard let fixture else { return }
        await find.keep(client: model.client, fixture: fixture, path: path) { saved in
            model.replace(saved)
            if !model.path.isEmpty { model.path.removeLast() }
            Haptics.success()
        }
    }

    private func leave() async {
        await find.abandon(model.client)
        if !model.path.isEmpty { model.path.removeLast() }
    }
}

@MainActor
final class FindModel: ObservableObject {
    @Published var phase: FindPhase = .ready
    @Published var mode: DetectMode = .binary
    @Published var includeOutliers = false
    @Published var useVision = false
    @Published var trustTheRoom = false
    @Published var roomDark: Bool?
    @Published var message: String?
    @Published var busy = false
    @Published var saving = false
    @Published var runId = ""
    @Published var frameNumber = 0
    @Published var frameCount = 0
    @Published var blobCount = 0
    @Published var assessments: [PixelAssessment] = []
    @Published private(set) var modelPixels: [MappedPixel] = []

    private var viewpoints: [[Sighting]] = []
    private var samples: [ModelSample] = []
    private var sessionId: String?
    private var fixtureId = ""
    private var cancelled = false

    var canStart: Bool { phase == .ready && !busy && (roomDark == true || trustTheRoom) }
    var hiddenCount: Int { assessments.filter(\.occluded).count }
    var canKeep: Bool {
        assessments.contains { $0.position != nil && !$0.missing && !$0.occluded && (!$0.outlier || includeOutliers) }
    }
    var canReplace: Bool {
        assessments.filter { $0.position != nil && !$0.missing && !$0.occluded && (!$0.outlier || includeOutliers) }.count >= 2
    }
    var summary: String {
        let kept = assessments.filter { $0.position != nil && !$0.outlier && !$0.missing && !$0.occluded }.count
        let odd = assessments.filter { $0.outlier && $0.position != nil }.count
        let dead = assessments.filter(\.missing).count
        if kept == 0 && odd == 0 && dead == 0 { return "No pixels were placed." }
        return "\(kept) moved · \(odd) look off · \(dead) never lit"
    }

    func prepare(_ fixture: Fixture?) {
        guard let fixture, runId.isEmpty || !fixture.runs.contains(where: { $0.id == runId }) else { return }
        runId = fixture.runs.first?.id ?? ""
    }

    func watchRoom(_ camera: DetectCamera) async {
        while !Task.isCancelled && phase == .ready {
            if let sight = camera.grab() {
                roomDark = roomBrightness(sight.bytes).dark
            }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
    }

    func capture(client: ServerClient, fixture: Fixture, run: Run, camera: DetectCamera, another: Bool) async {
        if !another {
            viewpoints = []
            assessments = []
            cancelled = false
        }
        busy = true
        message = nil
        phase = .running
        defer { busy = false }
        do {
            modelPixels = (try? compile(fixture))?.filter { pixel in
                pixel.controller == run.controllerId
                    && pixel.output == run.outputIndex
                    && run.segments.contains { $0.id == pixel.segment }
            } ?? []
            samples = modelPixels.map { ModelSample(index: $0.index, position: $0.position, segment: $0.segment) }
            let session = try await client.beginDetect(fixture: fixture.id, runId: run.id, mode: mode.rawValue)
            sessionId = session.sessionId
            fixtureId = fixture.id
            let plan = session.frames.compactMap { info -> CodeFrame? in
                guard let role = CodeRole(rawValue: info.role) else { return nil }
                return CodeFrame(step: info.step, role: role, bit: info.bit, lit: info.lit)
            }
            guard plan.count == session.frameCount else { throw ServerFailure(message: "The find plan did not make sense.") }
            frameCount = plan.count
            var shots: [(CodeFrame, CapturedSight)] = []
            for frame in plan {
                if cancelled { return }
                frameNumber = frame.step + 1
                let shown = try await client.stepDetect(fixture: fixture.id, session: session.sessionId, step: frame.step)
                guard shown.role == frame.role.rawValue else {
                    throw ServerFailure(message: "The lights and the camera got out of step.")
                }
                try await Task.sleep(nanoseconds: 160_000_000)
                guard var sight = await waitForFrame(camera) else {
                    throw ServerFailure(message: "The camera isn't ready. Point it at the strip and try again.")
                }
                if frame.role == .on && camera.centroids(sight, threshold: 180, vision: false).isEmpty {
                    try await Task.sleep(nanoseconds: 250_000_000)
                    if let again = camera.grab() { sight = again }
                }
                blobCount = camera.centroids(sight, threshold: 180, vision: useVision).count
                shots.append((frame, sight))
                if frame.role == .off && !trustTheRoom && !roomBrightness(sight.bytes).dark {
                    try? await client.finishDetect(fixture: fixture.id, session: session.sessionId)
                    sessionId = nil
                    phase = .ready
                    message = "Still too bright. Turn the room lights off, then start again."
                    Haptics.warning()
                    return
                }
            }
            try? await client.finishDetect(fixture: fixture.id, session: session.sessionId)
            sessionId = nil
            if cancelled { return }
            let off = shots.first { $0.0.role == .off }?.1.bytes ?? []
            let on = shots.first { $0.0.role == .on }?.1.bytes ?? []
            let threshold = shots.count >= 2 ? thresholdFromReference(off: off, on: on) : 180
            let scale = shots.first?.1.scale ?? 1
            let observations = shots.map { frame, sight in
                camera.centroids(sight, threshold: threshold, vision: useVision)
            }
            let decoded = decodeBlobs(frames: plan, observations: observations, matchRadius: Double(max(12, 8 * scale)))
            guard let posed = shots.first(where: { $0.0.role == .on })?.1 ?? shots.last?.1 else {
                throw ServerFailure(message: "No picture was kept.")
            }
            let sightings = decoded.compactMap { blob -> Sighting? in
                guard let index = blob.index else { return nil }
                return Sighting(
                    index: index,
                    ray: rayFromPixel(u: blob.centroid.x, v: blob.centroid.y, intrinsics: posed.intrinsics, pose: posed.pose)
                )
            }
            viewpoints.append(sightings)
            let asking = viewpoints.count == 1
            assessments = assessPixels(model: samples, viewpoints: viewpoints, finished: !asking)
            if asking && assessments.contains(where: \.occluded) {
                phase = .hidden
                Haptics.warning()
            } else {
                assessments = assessPixels(model: samples, viewpoints: viewpoints, finished: true)
                phase = .preview
                Haptics.success()
            }
        } catch {
            if cancelled {
                self.sessionId = nil
                return
            }
            if let sessionId {
                try? await client.finishDetect(fixture: fixtureId.isEmpty ? fixture.id : fixtureId, session: sessionId)
            }
            self.sessionId = nil
            message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
            phase = .ready
            Haptics.warning()
        }
    }

    func finishWithWhatWeHave() {
        assessments = assessPixels(model: samples, viewpoints: viewpoints, finished: true)
        phase = .preview
    }

    func cancel(_ client: ServerClient) async {
        cancelled = true
        await abandon(client)
        phase = .ready
        frameNumber = 0
        blobCount = 0
    }

    func abandon(_ client: ServerClient) async {
        guard let sessionId else { return }
        self.sessionId = nil
        try? await client.finishDetect(fixture: fixtureId, session: sessionId)
    }

    func keep(client: ServerClient, fixture: Fixture, path: Bool, saved: (Fixture) -> Void) async {
        guard !runId.isEmpty else { return }
        saving = true
        defer { saving = false }
        let updated = path
            ? applyingDetectedPath(fixture, runId: runId, assessments: assessments, includeOutliers: includeOutliers)
            : applyingCorrections(fixture, runId: runId, assessments: assessments, includeOutliers: includeOutliers)
        do {
            let stored = try await client.update(updated)
            saved(stored)
        } catch {
            message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
            Haptics.warning()
        }
    }

    func previewCloud() -> (pixels: [MappedPixel], colors: [RGB]) {
        let corrections = assessments.compactMap { item -> (MappedPixel, RGB)? in
            guard let position = item.position, !item.missing, !item.occluded else { return nil }
            let pixel = MappedPixel(
                fixture: "", controller: "", output: 0, index: item.index,
                x: position.x, y: position.y, z: position.z, segment: item.segment, u: 0
            )
            let color = item.outlier ? RGB(255, 110, 70) : RGB(255, 188, 70)
            return (pixel, color)
        }
        var colors = modelPixels.map { pixel -> RGB in
            assessments.first { $0.index == pixel.index }?.missing == true ? RGB(120, 48, 42) : RGB(58, 58, 62)
        }
        colors.append(contentsOf: corrections.map(\.1))
        return (modelPixels + corrections.map(\.0), colors)
    }

    private func waitForFrame(_ camera: DetectCamera) async -> CapturedSight? {
        for _ in 0..<12 {
            if let sight = camera.grab() { return sight }
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        return nil
    }
}

enum FindPhase {
    case ready
    case running
    case hidden
    case preview
}
