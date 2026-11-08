import MapperGeometry
import SwiftUI

struct EditFixtureSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    var onLive: (Fixture) -> Void

    @State private var draft: Fixture
    @State private var preview: [MappedPixel]
    @State private var coordinates: [String: AxisText]
    @State private var lengths: [String: String]
    @State private var overrides: [String: String]
    @State private var turns: [String: String]
    @State private var radiusStart: [String: String]
    @State private var radiusEnd: [String: String]
    @State private var stripLength = "1"
    @State private var stripCount = "60"
    @State private var saveTask: Task<Void, Never>?

    init(fixture: Fixture, onLive: @escaping (Fixture) -> Void) {
        self.onLive = onLive
        _draft = State(initialValue: fixture)
        _preview = State(initialValue: (try? compile(fixture)) ?? [])
        _coordinates = State(initialValue: Self.axes(for: fixture))
        _lengths = State(initialValue: Self.lengths(for: fixture))
        _overrides = State(initialValue: Self.overrides(for: fixture))
        _turns = State(initialValue: Self.helix(fixture, \SegmentParams.turns))
        _radiusStart = State(initialValue: Self.helix(fixture, \SegmentParams.radiusStartMm))
        _radiusEnd = State(initialValue: Self.helix(fixture, \SegmentParams.radiusEndMm))
    }

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            VStack(alignment: .leading, spacing: 14) {
                Text("Edit")
                    .font(.title.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                Text(draft.name)
                    .font(.subheadline)
                    .foregroundStyle(Theme.muted)
                PixelCloudView(pixels: preview, colors: preview.map { _ in RGB(255, 180, 60) })
                    .frame(height: 200)
                    .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        if draft.waypoints.isEmpty {
                            empty
                        } else {
                            points
                            segments
                        }
                    }
                    .padding(.bottom, 12)
                }
                .scrollDismissesKeyboard(.interactively)
                BigButton(title: "Done") { Task { await finish() } }
            }
            .padding(20)
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
    }

    private var empty: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("A straight strip, no camera.")
                .foregroundStyle(Theme.muted)
            labeled("Length, metres", text: $stripLength)
            labeled("Pixels", text: $stripCount)
            BigButton(title: "Make a straight strip", prominent: false) { makeStrip() }
        }
    }

    private var points: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Points")
                .font(.headline)
                .foregroundStyle(Theme.ink)
            ForEach(Array(draft.waypoints.enumerated()), id: \.element.id) { index, waypoint in
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(waypoint.label ?? "Point \(index + 1)")
                            .foregroundStyle(Theme.ink)
                        Spacer()
                        Button {
                            deletePoint(waypoint.id)
                        } label: {
                            Image(systemName: "trash")
                                .foregroundStyle(Theme.muted)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Delete point \(index + 1)")
                    }
                    HStack(spacing: 8) {
                        axisField(waypoint.id, position: \Vec3.x, text: \AxisText.x, prompt: "x")
                        axisField(waypoint.id, position: \Vec3.y, text: \AxisText.y, prompt: "y")
                        axisField(waypoint.id, position: \Vec3.z, text: \AxisText.z, prompt: "z")
                    }
                }
                .padding(12)
                .background(Theme.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            Button("Add point") { addPoint() }
                .font(.body.weight(.semibold))
                .foregroundStyle(Theme.amber)
                .buttonStyle(.plain)
        }
    }

    private var segments: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Segments")
                .font(.headline)
                .foregroundStyle(Theme.ink)
            ForEach(draft.runs) { run in
                ForEach(run.segments) { segment in
                    segmentCard(segment)
                }
            }
        }
    }

    private func segmentCard(_ segment: Segment) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(segmentLabel(segment))
                    .foregroundStyle(Theme.ink)
                Spacer()
                Menu(segment.kind.title) {
                    ForEach(SegmentKind.allCases, id: \.self) { kind in
                        Button(kind.title) { setKind(segment.id, kind) }
                    }
                }
                .foregroundStyle(Theme.amber)
            }
            if segment.kind == .straight || segment.kind == .polyline || segment.kind == .jump {
                labeled("Length, metres", text: lengthBinding(segment.id)) { value in
                    setLength(segment.id, value)
                }
            }
            if segment.kind == .helix {
                Text(helixCaption(segment))
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
                labeled("Turns", text: helixBinding(segment.id, .turns)) { value in
                    setHelix(segment.id, turns: value, start: nil, end: nil)
                }
                labeled("Radius start, mm", text: helixBinding(segment.id, .radiusStart)) { value in
                    setHelix(segment.id, turns: nil, start: value, end: nil)
                }
                labeled("Radius end, mm", text: helixBinding(segment.id, .radiusEnd)) { value in
                    setHelix(segment.id, turns: nil, start: nil, end: value)
                }
                Button(segment.params.handedness == "cw" ? "Clockwise" : "Counter-clockwise") {
                    toggleHand(segment.id)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Theme.amber)
            }
            if segment.kind != .jump {
                labeled("Pixels on this span", text: overrideBinding(segment.id)) { value in
                    setOverride(segment.id, Int(value))
                }
            }
        }
        .padding(12)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private func axisField(
        _ id: String,
        position: WritableKeyPath<Vec3, Double>,
        text: WritableKeyPath<AxisText, String>,
        prompt: String
    ) -> some View {
        TextField(prompt, text: axisBinding(id, text))
            .keyboardType(.decimalPad)
            .font(.body.monospacedDigit())
            .foregroundStyle(Theme.ink)
            .padding(10)
            .background(Theme.background, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .onChange(of: coordinates[id]?[keyPath: text] ?? "") { _, value in
                guard let number = Double(value) else { return }
                setAxis(id, position, number)
            }
    }

    private func labeled(_ title: String, text: Binding<String>, apply: ((Double) -> Void)? = nil) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(Theme.muted)
            TextField(title, text: text)
                .keyboardType(.decimalPad)
                .font(.body.monospacedDigit())
                .foregroundStyle(Theme.ink)
                .padding(10)
                .background(Theme.background, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .onChange(of: text.wrappedValue) { _, value in
                    if let number = Double(value) {
                        apply?(number)
                    } else if value.isEmpty && title == "Pixels on this span" {
                        apply?(0)
                    }
                }
        }
    }

    private func makeStrip() {
        guard let length = Double(stripLength), let count = Int(stripCount), length > 0, count > 0 else {
            model.message = "Enter a length and a pixel count."
            return
        }
        let pitch = length / Double(count) * 1000
        guard pitch > 0, pitch <= 1000 else {
            model.message = "That length needs a pitch over 1 m. Use more pixels or a shorter strip."
            return
        }
        let start = UUID().uuidString
        let end = UUID().uuidString
        let controller = model.controllers.first
        let output = controller?.outputs.first
        draft.waypoints = [
            Waypoint(id: start, position: Vec3(0, 0, 0), source: .typed, confidence: 1, label: "Start"),
            Waypoint(id: end, position: Vec3(0, length, 0), source: .typed, confidence: 1, label: "End"),
        ]
        draft.runs = [
            Run(
                id: UUID().uuidString,
                controllerId: controller?.id ?? "imported",
                outputIndex: output?.index ?? 0,
                startIndex: 0,
                pixelCount: count,
                pitchMm: pitch,
                colorOrder: output?.colorOrder ?? "GRB",
                segments: [
                    Segment(
                        id: UUID().uuidString,
                        kind: .straight,
                        waypointIds: [start, end],
                        params: SegmentParams()
                    )
                ]
            )
        ]
        coordinates = Self.axes(for: draft)
        lengths = Self.lengths(for: draft)
        overrides = Self.overrides(for: draft)
        publish()
    }

    private func addPoint() {
        let origin = draft.waypoints.last?.position ?? .zero
        let position = origin + Vec3(0.1, 0, 0)
        let id = UUID().uuidString
        draft.waypoints.append(Waypoint(id: id, position: position, source: .typed, confidence: 1, label: nil))
        coordinates[id] = AxisText(x: format(position.x), y: format(position.y), z: format(position.z))
        if draft.runs.isEmpty {
            draft.runs = [blankRun()]
        }
        let runIndex = draft.runs.count - 1
        if var segment = draft.runs[runIndex].segments.last, segment.kind == .polyline || segment.waypointIds.count < 2 {
            segment.waypointIds.append(id)
            draft.runs[runIndex].segments[draft.runs[runIndex].segments.count - 1] = segment
        } else if let previous = draft.runs[runIndex].segments.last?.waypointIds.last ?? draft.waypoints.dropLast().last?.id {
            let fresh = Segment(id: UUID().uuidString, kind: .straight, waypointIds: [previous, id], params: SegmentParams())
            draft.runs[runIndex].segments.append(fresh)
            lengths[fresh.id] = format(0.1)
            overrides[fresh.id] = ""
        } else {
            let fresh = Segment(id: UUID().uuidString, kind: .straight, waypointIds: [id], params: SegmentParams())
            draft.runs[runIndex].segments = [fresh]
        }
        publish()
    }

    private func deletePoint(_ id: String) {
        draft.waypoints.removeAll { $0.id == id }
        coordinates[id] = nil
        for runIndex in draft.runs.indices {
            for segmentIndex in draft.runs[runIndex].segments.indices {
                draft.runs[runIndex].segments[segmentIndex].waypointIds.removeAll { $0 == id }
            }
            draft.runs[runIndex].segments.removeAll { $0.waypointIds.count < 2 }
        }
        publish()
    }

    private func setAxis(_ id: String, _ axis: WritableKeyPath<Vec3, Double>, _ value: Double) {
        guard let index = draft.waypoints.firstIndex(where: { $0.id == id }) else { return }
        draft.waypoints[index].position[keyPath: axis] = value
        draft.waypoints[index].source = .typed
        publish()
    }

    private func setKind(_ id: String, _ kind: SegmentKind) {
        mutate(id) { segment in
            segment.kind = kind
            if kind == .helix {
                if segment.params.turns == nil { segment.params.turns = 4 }
                if segment.params.radiusStartMm == nil { segment.params.radiusStartMm = 80 }
                if segment.params.radiusEndMm == nil { segment.params.radiusEndMm = 80 }
                if segment.params.handedness == nil { segment.params.handedness = "ccw" }
                turns[id] = format(segment.params.turns ?? 4)
                radiusStart[id] = format(segment.params.radiusStartMm ?? 80)
                radiusEnd[id] = format(segment.params.radiusEndMm ?? 80)
            } else {
                segment.params = SegmentParams()
            }
            if kind == .jump {
                segment.pixelCountOverride = nil
                overrides[id] = ""
            }
        }
    }

    private func setLength(_ id: String, _ meters: Double) {
        guard meters > 0, let segment = segment(id) else { return }
        let ids = segment.waypointIds
        guard ids.count >= 2, let start = position(ids[0]) else { return }
        let current = pathLength(segment)
        guard current > 1e-9 else { return }
        let scale = meters / current
        for waypointId in ids.dropFirst() {
            guard let point = position(waypointId) else { continue }
            setPosition(waypointId, start + (point - start) * scale)
        }
        publish()
    }

    private func setOverride(_ id: String, _ count: Int?) {
        mutate(id) { segment in
            if let count, count > 0 {
                segment.pixelCountOverride = count
            } else {
                segment.pixelCountOverride = nil
            }
        }
    }

    private func setHelix(_ id: String, turns turnValue: Double?, start: Double?, end: Double?) {
        mutate(id) { segment in
            if let turnValue { segment.params.turns = turnValue }
            if let start { segment.params.radiusStartMm = start }
            if let end { segment.params.radiusEndMm = end }
            if segment.params.turns == nil { segment.params.turns = 4 }
            if segment.params.radiusStartMm == nil { segment.params.radiusStartMm = 80 }
            if segment.params.radiusEndMm == nil { segment.params.radiusEndMm = segment.params.radiusStartMm }
            if segment.params.handedness == nil { segment.params.handedness = "ccw" }
            segment.kind = .helix
        }
    }

    private func toggleHand(_ id: String) {
        mutate(id) { segment in
            segment.params.handedness = segment.params.handedness == "cw" ? "ccw" : "cw"
            if segment.params.turns == nil { segment.params.turns = 4 }
            if segment.params.radiusStartMm == nil { segment.params.radiusStartMm = 80 }
            if segment.params.radiusEndMm == nil { segment.params.radiusEndMm = 80 }
        }
    }

    private func mutate(_ id: String, _ body: (inout Segment) -> Void) {
        for runIndex in draft.runs.indices {
            guard let index = draft.runs[runIndex].segments.firstIndex(where: { $0.id == id }) else { continue }
            body(&draft.runs[runIndex].segments[index])
            publish()
            return
        }
    }

    private func setPosition(_ id: String, _ position: Vec3) {
        guard let index = draft.waypoints.firstIndex(where: { $0.id == id }) else { return }
        draft.waypoints[index].position = position
        draft.waypoints[index].source = .typed
        coordinates[id] = AxisText(x: format(position.x), y: format(position.y), z: format(position.z))
    }

    private func publish() {
        do {
            preview = try compile(draft)
            model.message = nil
            onLive(draft)
        } catch {
            model.message = String(describing: error)
            return
        }
        scheduleSave()
    }

    private func scheduleSave() {
        saveTask?.cancel()
        let snapshot = draft
        saveTask = Task {
            try? await Task.sleep(nanoseconds: 400_000_000)
            if Task.isCancelled { return }
            await save(snapshot)
        }
    }

    private func finish() async {
        saveTask?.cancel()
        do {
            preview = try compile(draft)
        } catch {
            model.message = String(describing: error)
            return
        }
        onLive(draft)
        await save(draft)
        dismiss()
    }

    private func save(_ snapshot: Fixture) async {
        do {
            let saved = try await model.client.update(snapshot)
            model.replace(saved)
            model.message = nil
        } catch {
            model.replace(snapshot)
            model.message = error.localizedDescription
        }
    }

    private func blankRun() -> Run {
        let controller = model.controllers.first
        let output = controller?.outputs.first
        return Run(
            id: UUID().uuidString,
            controllerId: controller?.id ?? "imported",
            outputIndex: output?.index ?? 0,
            startIndex: 0,
            pixelCount: 0,
            pitchMm: 1000.0 / 60.0,
            colorOrder: output?.colorOrder ?? "GRB",
            segments: []
        )
    }

    private func segment(_ id: String) -> Segment? {
        draft.runs.lazy.flatMap(\.segments).first { $0.id == id }
    }

    private func position(_ id: String) -> Vec3? {
        draft.waypoints.first { $0.id == id }?.position
    }

    private func pathLength(_ segment: Segment) -> Double {
        let pts = segment.waypointIds.compactMap { position($0) }
        guard pts.count >= 2 else { return 0 }
        switch segment.kind {
        case .straight, .polyline, .jump:
            return polylineLength(pts)
        case .curve:
            return curveLength(pts)
        case .helix:
            return polylineLength([pts[0], pts[pts.count - 1]])
        }
    }

    private func segmentLabel(_ segment: Segment) -> String {
        let metres = pathLength(segment)
        if segment.kind == .helix, let turns = segment.params.turns, let radius = segment.params.radiusStartMm {
            let arc = helixLength(turns: turns, radius: radius / 1000, rise: metres)
            return String(format: "Helix · %.2f m", arc)
        }
        return String(format: "%@ · %.2f m", segment.kind.title, metres)
    }

    private func helixCaption(_ segment: Segment) -> String {
        let hand = segment.params.handedness == "cw" ? "clockwise" : "counter-clockwise"
        return "Looking up the axis, \(hand)."
    }

    private func axisBinding(_ id: String, _ axis: WritableKeyPath<AxisText, String>) -> Binding<String> {
        Binding(
            get: { coordinates[id]?[keyPath: axis] ?? "" },
            set: { value in
                var text = coordinates[id] ?? AxisText(x: "", y: "", z: "")
                text[keyPath: axis] = value
                coordinates[id] = text
            }
        )
    }

    private func lengthBinding(_ id: String) -> Binding<String> {
        Binding(
            get: { lengths[id] ?? "" },
            set: { lengths[id] = $0 }
        )
    }

    private func overrideBinding(_ id: String) -> Binding<String> {
        Binding(
            get: { overrides[id] ?? "" },
            set: { overrides[id] = $0 }
        )
    }

    private func helixBinding(_ id: String, _ field: HelixField) -> Binding<String> {
        Binding(
            get: {
                switch field {
                case .turns: return turns[id] ?? ""
                case .radiusStart: return radiusStart[id] ?? ""
                case .radiusEnd: return radiusEnd[id] ?? ""
                }
            },
            set: { value in
                switch field {
                case .turns: turns[id] = value
                case .radiusStart: radiusStart[id] = value
                case .radiusEnd: radiusEnd[id] = value
                }
            }
        )
    }

    private static func axes(for fixture: Fixture) -> [String: AxisText] {
        var result: [String: AxisText] = [:]
        for waypoint in fixture.waypoints {
            result[waypoint.id] = AxisText(
                x: format(waypoint.position.x),
                y: format(waypoint.position.y),
                z: format(waypoint.position.z)
            )
        }
        return result
    }

    private static func lengths(for fixture: Fixture) -> [String: String] {
        var result: [String: String] = [:]
        let lookup = Dictionary(uniqueKeysWithValues: fixture.waypoints.map { ($0.id, $0.position) })
        for run in fixture.runs {
            for segment in run.segments {
                let pts = segment.waypointIds.compactMap { lookup[$0] }
                guard pts.count >= 2 else { continue }
                let metres: Double
                switch segment.kind {
                case .curve:
                    metres = curveLength(pts)
                case .straight, .polyline, .jump, .helix:
                    metres = segment.kind == .helix ? distance(pts[0], pts[pts.count - 1]) : polylineLength(pts)
                }
                result[segment.id] = format(metres)
            }
        }
        return result
    }

    private static func overrides(for fixture: Fixture) -> [String: String] {
        var result: [String: String] = [:]
        for run in fixture.runs {
            for segment in run.segments {
                result[segment.id] = segment.pixelCountOverride.map(String.init) ?? ""
            }
        }
        return result
    }

    private static func helix(_ fixture: Fixture, _ field: KeyPath<SegmentParams, Double?>) -> [String: String] {
        var result: [String: String] = [:]
        for run in fixture.runs {
            for segment in run.segments {
                result[segment.id] = segment.params[keyPath: field].map(format) ?? ""
            }
        }
        return result
    }
}

private struct AxisText {
    var x: String
    var y: String
    var z: String
}

private enum HelixField {
    case turns
    case radiusStart
    case radiusEnd
}

private func format(_ value: Double) -> String {
    if value == 0 { return "0" }
    return String(format: "%.4f", value)
}
