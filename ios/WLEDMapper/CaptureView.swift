import ARKit
import MapperGeometry
import Metal
import RealityKit
import SwiftUI
import UIKit

struct CaptureScreen: View {
    @EnvironmentObject private var model: AppModel
    var fixtureId: String
    @StateObject private var capture = CaptureModel()
    @State private var showNudge = false
    @State private var showAdjust = false

    var body: some View {
        ZStack {
            CaptureCanvas(session: capture.session, waypoints: capture.fixture.waypoints, fixtureId: fixtureId)
                .ignoresSafeArea()
                .onTapGesture { place() }
            reticle
            VStack(spacing: 12) {
                topBar
                Spacer().allowsHitTesting(false)
                shapes
                if capture.shape == .helix { helixBar }
                if showNudge { nudgeBar }
                if let notice = capture.notice {
                    Text(notice)
                        .font(.footnote.weight(.medium))
                        .foregroundStyle(Theme.warn)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                adjustGroup
                countLine
                HStack(spacing: 10) {
                    BigButton(title: "Undo", prominent: false) { capture.undo() }
                        .disabled(capture.fixture.waypoints.isEmpty)
                    BigButton(title: capture.saving ? "Saving" : "Save") { Task { await save() } }
                }
            }
            .padding(16)
        }
        .accessibilityIdentifier("screen.capture")
        .toolbar(.hidden, for: .navigationBar)
        .onAppear { capture.load(model.fixture(fixtureId), controllers: model.controllers) }
        .task {
            if model.controllers.isEmpty {
                model.controllers = (try? await model.client.controllers()) ?? model.controllers
                capture.load(model.fixture(fixtureId), controllers: model.controllers)
            }
        }
    }

    private var reticle: some View {
        ZStack {
            Circle().stroke(Theme.amber, lineWidth: 2).frame(width: 36, height: 36)
            Circle().fill(Theme.amber).frame(width: 6, height: 6)
        }
        .allowsHitTesting(false)
    }

    private var topBar: some View {
        HStack {
            Button {
                model.path.removeLast()
            } label: {
                Image(systemName: "chevron.left")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                    .frame(width: 44, height: 44)
                    .background(.black.opacity(0.45), in: Circle())
            }
            .buttonStyle(.plain)
            Spacer()
            Text(ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) ? "LiDAR" : "Planes")
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.ink)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(.black.opacity(0.45), in: Capsule())
        }
    }

    private var shapes: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(SegmentKind.allCases, id: \.self) { kind in
                    Button {
                        Haptics.undo()
                        capture.shape = kind
                    } label: {
                        Text(kind.title)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(capture.shape == kind ? Color.black : Theme.ink)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 10)
                            .background(capture.shape == kind ? Theme.amber : Color.black.opacity(0.45), in: Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private var helixBar: some View {
        VStack(spacing: 8) {
            HStack {
                Text(capture.turnLabel)
                Spacer()
                Text(capture.handedness == "ccw" ? "CCW looking up" : "CW looking up")
                Button(capture.handedness == "ccw" ? "Clockwise" : "Counter") {
                    capture.handedness = capture.handedness == "ccw" ? "cw" : "ccw"
                    capture.refreshHelixParams()
                }
            }
            .font(.subheadline.weight(.medium))
            .foregroundStyle(Theme.ink)
            Slider(value: $capture.radiusMm, in: 10...400, step: 1)
                .tint(Theme.amber)
                .onChange(of: capture.radiusMm) { _, _ in
                    capture.refreshHelixParams()
                }
            Text("Tap once per turn, on the same side. Radius \(Int(capture.radiusMm)) mm.")
                .font(.caption)
                .foregroundStyle(Theme.muted)
        }
        .padding(12)
        .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private var nudgeBar: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Depth nudge")
                .font(.caption.weight(.medium))
                .foregroundStyle(Theme.muted)
            Slider(value: $capture.nudge, in: -0.4...0.6)
                .tint(Theme.amber)
                .onChange(of: capture.nudge) { _, _ in
                    capture.applyNudge()
                }
        }
        .padding(12)
        .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private var adjustGroup: some View {
        DisclosureGroup("Adjust", isExpanded: $showAdjust) {
            VStack(alignment: .leading, spacing: 10) {
                Button("Scale pitch to match the controller") {
                    capture.scalePitch()
                    Haptics.success()
                }
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.ink)
                Stepper(value: Binding(get: { capture.spanOverride }, set: { capture.setSpanOverride($0) }), in: 0...5000) {
                    Text("This span: \(capture.spanOverride) pixels")
                        .font(.subheadline)
                        .foregroundStyle(Theme.ink)
                }
                Button("Use the measured count") { capture.clearSpanOverride() }
                    .font(.caption.weight(.medium))
                    .foregroundStyle(Theme.muted)
            }
            .padding(.top, 8)
        }
        .font(.subheadline.weight(.semibold))
        .foregroundStyle(Theme.ink)
        .padding(12)
        .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private var countLine: some View {
        HStack {
            Menu {
                Button("30 / m") { capture.setPitch(1000 / 30) }
                Button("60 / m") { capture.setPitch(1000 / 60) }
                Button("96 / m") { capture.setPitch(1000 / 96) }
                Button("144 / m") { capture.setPitch(1000 / 144) }
            } label: {
                Text(capture.pitchLabel)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Theme.ink)
            }
            Menu {
                ForEach(model.controllers) { controller in
                    if controller.outputs.isEmpty {
                        Button(controller.name) { capture.assign(controller, output: nil) }
                    }
                    ForEach(controller.outputs) { output in
                        Button("\(controller.name) · out \(output.index + 1) · \(output.pixelCount)") {
                            capture.assign(controller, output: output)
                        }
                    }
                }
            } label: {
                Text(capture.outputLabel)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Theme.ink)
            }
            Spacer()
            Button(showNudge ? "Hide nudge" : "Nudge") { showNudge.toggle() }
                .font(.subheadline.weight(.medium))
                .foregroundStyle(Theme.ink)
            if capture.countOk && (capture.activeRun?.pixelCount ?? 0) > 0 {
                Button("Next") { capture.beginNext() }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.amber)
            }
            Text(capture.countLabel)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(capture.countOk ? Theme.good : Theme.amber)
                .accessibilityIdentifier("capture.count")
        }
        .padding(.horizontal, 4)
    }

    private func place() {
        guard let sample = capture.session.sample() else {
            capture.notice = "The camera is not tracking yet."
            Haptics.warning()
            return
        }
        capture.place(sample)
        if sample.source == .estimated {
            Haptics.warning()
        } else {
            Haptics.place()
        }
        guard let run = capture.activeRun, let index = capture.lastIndex else { return }
        Task {
            try? await model.client.highlight(
                fixture: capture.fixture.id,
                controller: run.controllerId,
                output: run.outputIndex,
                index: index
            )
        }
    }

    private func save() async {
        capture.saving = true
        defer { capture.saving = false }
        var fixture = capture.prepared()
        if fixture.frame == nil {
            fixture = normalise(fixture)
        }
        capture.session.archiveWorldMap(id: fixture.id)
        do {
            let saved = try await model.client.update(fixture)
            model.replace(saved)
            Haptics.success()
            if !model.path.isEmpty { model.path.removeLast() }
            if !model.path.contains(.fixture(saved.id)) {
                model.path.append(.fixture(saved.id))
            }
        } catch {
            model.message = error.localizedDescription
            Haptics.warning()
        }
    }
}

@MainActor
final class CaptureModel: ObservableObject {
    @Published var fixture = Fixture(id: "draft", name: "Fixture", surfaceHint: .freeform, waypoints: [], runs: [])
    @Published var shape: SegmentKind = .straight
    @Published var radiusMm = 80.0
    @Published var handedness = "ccw"
    @Published var nudge = 0.0
    @Published var saving = false
    @Published var notice: String?
    let session = CaptureSession()
    private var history: [Fixture] = []
    private var active = 0
    private var lastHit: Vec3?
    private var lastCamera: Vec3?
    private var inward: Vec3?

    var activeRun: Run? {
        fixture.runs.indices.contains(active) ? fixture.runs[active] : nil
    }

    var lastIndex: Int? {
        guard let run = activeRun else { return nil }
        let count = (try? compile(fixture).filter { $0.segmentIds(in: run) }.count) ?? 0
        return max(count - 1, 0) + run.startIndex
    }

    var pitchLabel: String {
        guard let pitch = activeRun?.pitchMm, pitch > 0 else { return "60/m" }
        return "\(Int((1000 / pitch).rounded()))/m"
    }

    var outputLabel: String {
        guard let run = activeRun else { return "Choose output" }
        return "\(run.controllerId) · out \(run.outputIndex + 1)"
    }

    var countLabel: String {
        let placed = (try? compile(fixture).filter { pixel in
            guard let run = activeRun else { return false }
            return pixel.segmentIds(in: run)
        }.count) ?? 0
        let expected = activeRun?.pixelCount ?? 0
        return "\(placed) placed · \(expected) on controller"
    }

    var turnLabel: String {
        let turns = helixPoints.map { max($0 - 1, 0) } ?? 0
        return turns == 1 ? "1 turn" : "\(turns) turns"
    }

    var spanOverride: Int {
        guard fixture.runs.indices.contains(active), let segment = fixture.runs[active].segments.last else { return 0 }
        if let value = segment.pixelCountOverride { return value }
        return (try? compile(fixture).filter { $0.segment == segment.id }.count) ?? 0
    }

    private var helixPoints: Int? {
        guard shape == .helix, fixture.runs.indices.contains(active), let segment = fixture.runs[active].segments.last, segment.kind == .helix else {
            return nil
        }
        return segment.waypointIds.count
    }

    var countOk: Bool {
        let expected = activeRun?.pixelCount ?? 0
        guard expected > 0 else { return true }
        let placed = (try? compile(fixture).filter { pixel in
            guard let run = activeRun else { return false }
            return pixel.segmentIds(in: run)
        }.count) ?? 0
        return Double(abs(placed - expected)) / Double(expected) <= 0.03
    }

    func load(_ existing: Fixture?, controllers: [ControllerRecord]) {
        guard fixture.waypoints.isEmpty, let existing else { return }
        fixture = existing
        if fixture.runs.isEmpty, let controller = controllers.first {
            assign(controller, output: controller.outputs.first)
        }
        active = max(fixture.runs.count - 1, 0)
    }

    func assign(_ controller: ControllerRecord, output: ControllerOutput?) {
        let chosen = output ?? ControllerOutput(index: 0, start: 0, pixelCount: 0, pin: nil, colorOrder: "GRB")
        if fixture.runs.isEmpty {
            fixture.runs = [makeRun(controller, chosen)]
            active = 0
            return
        }
        fixture.runs[active].controllerId = controller.id
        fixture.runs[active].outputIndex = chosen.index
        fixture.runs[active].startIndex = 0
        fixture.runs[active].pixelCount = chosen.pixelCount
        fixture.runs[active].colorOrder = chosen.colorOrder
    }

    func place(_ sample: CaptureSample) {
        guard activeRun != nil else { return }
        history.append(fixture)
        if history.count > 40 { history.removeFirst() }
        lastHit = sample.position
        lastCamera = sample.camera
        nudge = 0
        if sample.source == .estimated {
            notice = "No surface under the reticle. This point is a guess — use Nudge."
        } else {
            notice = nil
        }
        let flat = sample.position - sample.camera
        let horizontal = Vec3(flat.x, 0, flat.z)
        if horizontal.length() > 1e-6 {
            inward = horizontal.normalized()
        }
        let stored = storedPosition(sample.position)
        let waypoint = Waypoint(
            id: UUID().uuidString,
            position: stored,
            source: sample.source,
            confidence: sample.confidence,
            label: nil
        )
        fixture.waypoints.append(waypoint)
        append(waypoint.id)
        if shape == .helix, session.lidar, let radius = session.trunkRadius(at: sample.position, limit: max(0.5, radiusMm / 1000 * 3)) {
            radiusMm = radius
            refreshHelixParams()
        }
    }

    func scalePitch() {
        guard fixture.runs.indices.contains(active) else { return }
        guard let pitch = try? pitchMillimetres(matching: fixture.runs[active], waypoints: fixture.waypoints) else { return }
        fixture.runs[active].pitchMm = pitch
    }

    func setSpanOverride(_ count: Int) {
        guard fixture.runs.indices.contains(active), !fixture.runs[active].segments.isEmpty else { return }
        let index = fixture.runs[active].segments.count - 1
        fixture.runs[active].segments[index].pixelCountOverride = count
    }

    func clearSpanOverride() {
        guard fixture.runs.indices.contains(active), !fixture.runs[active].segments.isEmpty else { return }
        let index = fixture.runs[active].segments.count - 1
        fixture.runs[active].segments[index].pixelCountOverride = nil
    }

    func applyNudge() {
        guard let hit = lastHit, let camera = lastCamera, let last = fixture.waypoints.indices.last else { return }
        let moved = nudgeTowardCamera(hit: hit, camera: camera, meters: nudge)
        fixture.waypoints[last].position = storedPosition(moved)
    }

    func undo() {
        guard let previous = history.popLast() else { return }
        fixture = previous
        Haptics.undo()
    }

    func prepared() -> Fixture { fixture }

    private func storedPosition(_ raw: Vec3) -> Vec3 {
        guard let frame = fixture.frame else { return raw }
        return applyFrame(raw, frame)
    }

    private func append(_ id: String) {
        guard fixture.runs.indices.contains(active) else { return }
        let segments = fixture.runs[active].segments
        if var last = segments.last, last.waypointIds.count < 2 || (!last.isClosed(for: shape) && last.kind == shape) {
            if last.waypointIds.count < 2 { last.kind = shape }
            last.waypointIds.append(id)
            last.params = params(points: last.waypointIds.count)
            fixture.runs[active].segments[segments.count - 1] = last
            return
        }
        var ids: [String] = []
        if let previous = segments.last, let shared = previous.waypointIds.last, previous.isSatisfied() {
            ids.append(shared)
        }
        ids.append(id)
        fixture.runs[active].segments.append(
            Segment(id: UUID().uuidString, kind: shape, waypointIds: ids, params: params(points: ids.count))
        )
    }

    private func params(points: Int) -> SegmentParams {
        guard shape == .helix else { return SegmentParams() }
        return SegmentParams(
            turns: Double(max(points - 1, 1)),
            radiusStartMm: radiusMm,
            radiusEndMm: radiusMm,
            handedness: handedness,
            inward: inward
        )
    }

    func refreshHelixParams() {
        guard fixture.runs.indices.contains(active),
              let index = fixture.runs[active].segments.indices.last,
              fixture.runs[active].segments[index].kind == .helix else { return }
        let points = fixture.runs[active].segments[index].waypointIds.count
        fixture.runs[active].segments[index].params = params(points: points)
    }

    func setPitch(_ millimetres: Double) {
        guard fixture.runs.indices.contains(active) else { return }
        fixture.runs[active].pitchMm = millimetres
    }

    func beginNext() {
        guard let run = activeRun else { return }
        var copy = run
        copy.id = UUID().uuidString
        copy.segments = []
        fixture.runs.append(copy)
        active = fixture.runs.count - 1
        Haptics.success()
    }

    private func makeRun(_ controller: ControllerRecord, _ output: ControllerOutput) -> Run {
        Run(
            id: UUID().uuidString,
            controllerId: controller.id,
            outputIndex: output.index,
            startIndex: 0,
            pixelCount: output.pixelCount,
            pitchMm: 1000.0 / 60.0,
            colorOrder: output.colorOrder,
            segments: []
        )
    }
}

private extension Segment {
    func isSatisfied() -> Bool {
        switch kind {
        case .straight, .jump, .helix: return waypointIds.count >= 2
        case .polyline: return waypointIds.count >= 2
        case .curve: return waypointIds.count >= 3
        }
    }

    func isClosed(for shape: SegmentKind) -> Bool {
        if kind != shape { return isSatisfied() }
        switch shape {
        case .straight, .jump: return waypointIds.count >= 2
        case .polyline, .curve, .helix: return false
        }
    }
}

private extension MappedPixel {
    func segmentIds(in run: Run) -> Bool {
        run.segments.contains { $0.id == segment } && controller == run.controllerId
    }
}

struct CaptureSample {
    var position: Vec3
    var camera: Vec3
    var confidence: Double
    var source: WaypointSource
}

final class CaptureSession: ObservableObject {
    @Published var lidar = false
    weak var view: ARView?
    private var started = false

    func start(_ view: ARView, fixtureId: String) {
        self.view = view
        guard !started else { return }
        started = true
        let config = ARWorldTrackingConfiguration()
        config.worldAlignment = .gravity
        config.planeDetection = [.horizontal, .vertical]
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            config.sceneReconstruction = .mesh
            lidar = true
        }
        if let map = Self.loadMap(fixtureId) {
            config.initialWorldMap = map
        }
        view.session.run(config, options: [.resetTracking, .removeExistingAnchors])
    }

    func sample() -> CaptureSample? {
        guard let view else { return nil }
        let point = CGPoint(x: view.bounds.midX, y: view.bounds.midY)
        let frame = view.session.currentFrame
        let camera = frame.map { transform in
            Vec3(Double(transform.camera.transform.columns.3.x), Double(transform.camera.transform.columns.3.y), Double(transform.camera.transform.columns.3.z))
        } ?? .zero
        let confidence = frame.map { Self.confidence($0.camera.trackingState) } ?? 0
        if let hit = view.raycast(from: point, allowing: .estimatedPlane, alignment: .any).first {
            let column = hit.worldTransform.columns.3
            return CaptureSample(
                position: Vec3(Double(column.x), Double(column.y), Double(column.z)),
                camera: camera,
                confidence: confidence,
                source: lidar ? .lidarSnapped : .tapped
            )
        }
        guard let transform = frame?.camera.transform else { return nil }
        let forward = SIMD3<Float>(-transform.columns.2.x, -transform.columns.2.y, -transform.columns.2.z)
        let origin = SIMD3<Float>(transform.columns.3.x, transform.columns.3.y, transform.columns.3.z)
        let dropped = origin + forward * 1.5
        return CaptureSample(
            position: Vec3(Double(dropped.x), Double(dropped.y), Double(dropped.z)),
            camera: camera,
            confidence: min(confidence, 0.2),
            source: .estimated
        )
    }

    func trunkRadius(at point: Vec3, limit: Double) -> Double? {
        guard let frame = view?.session.currentFrame else { return nil }
        var samples: [(Double, Double)] = []
        for anchor in frame.anchors {
            guard let mesh = anchor as? ARMeshAnchor else { continue }
            let source = mesh.geometry.vertices
            guard source.format == .float3 else { continue }
            let base = source.buffer.contents()
            for index in 0..<source.count {
                let pointer = base.advanced(by: index * source.stride + source.offset)
                let local = pointer.assumingMemoryBound(to: SIMD3<Float>.self).pointee
                let world = mesh.transform * SIMD4<Float>(local.x, local.y, local.z, 1)
                let dy = abs(world.y - Float(point.y))
                let horizontal = hypot(Double(world.x) - point.x, Double(world.z) - point.z)
                if dy < 0.05 && horizontal <= limit {
                    samples.append((Double(world.x), Double(world.z)))
                }
                if samples.count > 400 { break }
            }
        }
        guard let fit = try? robustCircle(samples, near: (point.x, point.z), maxDistance: limit) else { return nil }
        let centerDistance = hypot(fit.x - point.x, fit.z - point.z)
        if centerDistance > limit { return nil }
        return fit.radius * 1000
    }

    func archiveWorldMap(id: String) {
        view?.session.getCurrentWorldMap { map, _ in
            guard let map, let data = try? NSKeyedArchiver.archivedData(withRootObject: map, requiringSecureCoding: true) else { return }
            try? data.write(to: Self.url(id), options: .atomic)
        }
    }

    private static func confidence(_ state: ARCamera.TrackingState) -> Double {
        switch state {
        case .normal:
            return 1
        case .limited:
            return 0.4
        case .notAvailable:
            return 0
        }
    }

    private static func loadMap(_ id: String) -> ARWorldMap? {
        guard let data = try? Data(contentsOf: url(id)) else { return nil }
        return try? NSKeyedUnarchiver.unarchivedObject(ofClass: ARWorldMap.self, from: data)
    }

    private static func url(_ id: String) -> URL {
        let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("worldmaps", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appendingPathComponent("\(id).armap")
    }
}

struct CaptureCanvas: UIViewRepresentable {
    var session: CaptureSession
    var waypoints: [Waypoint]
    var fixtureId: String

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero)
        session.start(view, fixtureId: fixtureId)
        return view
    }

    func updateUIView(_ view: ARView, context: Context) {
        let signature = waypoints.map { "\($0.id):\($0.position.x):\($0.position.y):\($0.position.z)" }.joined(separator: "|")
        guard context.coordinator.signature != signature else { return }
        context.coordinator.signature = signature
        view.scene.anchors.removeAll()
        var previous: SIMD3<Float>?
        for waypoint in waypoints {
            let position = SIMD3<Float>(Float(waypoint.position.x), Float(waypoint.position.y), Float(waypoint.position.z))
            let anchor = AnchorEntity(world: position)
            let color = waypoint.source == .estimated
                ? UIColor(red: 0.55, green: 0.62, blue: 0.72, alpha: 1)
                : UIColor(red: 1, green: 0.74, blue: 0.32, alpha: 1)
            let sphere = ModelEntity(
                mesh: .generateSphere(radius: waypoint.source == .estimated ? 0.016 : 0.012),
                materials: [SimpleMaterial(color: color, isMetallic: false)]
            )
            anchor.addChild(sphere)
            view.scene.addAnchor(anchor)
            if let previous {
                let mid = (previous + position) / 2
                let length = max(simd_distance(previous, position), 0.001)
                let line = ModelEntity(
                    mesh: .generateBox(width: 0.004, height: 0.004, depth: length),
                    materials: [SimpleMaterial(color: .white, isMetallic: false)]
                )
                line.look(at: position, from: mid, relativeTo: nil)
                let lineAnchor = AnchorEntity(world: .zero)
                lineAnchor.addChild(line)
                view.scene.addAnchor(lineAnchor)
            }
            previous = position
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator {
        var signature = ""
    }
}
