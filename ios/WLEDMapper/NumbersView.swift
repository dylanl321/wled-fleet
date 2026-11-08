import MapperGeometry
import SwiftUI

struct NumbersScreen: View {
    @EnvironmentObject private var model: AppModel
    var fixtureId: String
    var previewOnly = false
    @State private var draft: Fixture?
    @State private var saving = false

    private var fixture: Fixture? { draft ?? model.fixture(fixtureId) }

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            VStack(alignment: .leading, spacing: 16) {
                Text(fixture?.name ?? "Fixture")
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                Text("Waypoints")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                    .accessibilityIdentifier("screen.numbers")
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        if let fixture {
                            ForEach(fixture.waypoints) { waypoint in
                                waypointRow(waypoint)
                            }
                            Text("Segments")
                                .font(.title3.weight(.semibold))
                                .foregroundStyle(Theme.ink)
                                .padding(.top, 8)
                            ForEach(fixture.runs) { run in
                                ForEach(run.segments) { segment in
                                    segmentRow(run: run, segment: segment)
                                }
                            }
                        }
                    }
                }
                if !previewOnly {
                    HStack(spacing: 10) {
                        BigButton(title: "Mark reference", prominent: false) { markReference() }
                        BigButton(title: "Spread drift", prominent: false) { spreadDrift() }
                    }
                    BigButton(title: saving ? "Saving" : "Save") { Task { await save() } }
                }
            }
            .padding(20)
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .navigationBar)
        .onAppear {
            if draft == nil { draft = model.fixture(fixtureId) }
        }
    }

    private func waypointRow(_ waypoint: Waypoint) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(waypoint.label ?? "Point")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.ink)
            HStack(spacing: 8) {
                numberField("X", waypoint.position.x) { update(waypoint.id, axis: 0, value: $0) }
                numberField("Y", waypoint.position.y) { update(waypoint.id, axis: 1, value: $0) }
                numberField("Z", waypoint.position.z) { update(waypoint.id, axis: 2, value: $0) }
            }
        }
        .padding(12)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private func segmentRow(run: Run, segment: Segment) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(segment.kind.title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.ink)
            if segment.kind == .helix {
                HStack {
                    numberField("Radius mm", segment.params.radiusStartMm ?? 80) { setRadius(run.id, segment.id, $0) }
                    numberField("Turns", segment.params.turns ?? 1) { setTurns(run.id, segment.id, $0) }
                }
            }
            Stepper(value: Binding(get: {
                segment.pixelCountOverride ?? 0
            }, set: { setOverride(run.id, segment.id, $0) }), in: 0...5000) {
                Text(segment.pixelCountOverride == nil ? "Measured count" : "\(segment.pixelCountOverride ?? 0) pixels")
                    .font(.subheadline)
                    .foregroundStyle(Theme.muted)
            }
        }
        .padding(12)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private func numberField(_ title: String, _ value: Double, set: @escaping (Double) -> Void) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(Theme.muted)
            TextField(title, value: Binding(get: { value }, set: set), format: .number.precision(.fractionLength(3)))
                .keyboardType(.numbersAndPunctuation)
                .font(.body.monospacedDigit())
                .foregroundStyle(Theme.ink)
        }
        .padding(8)
        .background(Theme.background, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    private func update(_ id: String, axis: Int, value: Double) {
        guard var fixture, let index = fixture.waypoints.firstIndex(where: { $0.id == id }) else { return }
        switch axis {
        case 0: fixture.waypoints[index].position.x = value
        case 1: fixture.waypoints[index].position.y = value
        default: fixture.waypoints[index].position.z = value
        }
        fixture.waypoints[index].source = .typed
        draft = fixture
    }

    private func setRadius(_ runId: String, _ segmentId: String, _ value: Double) {
        edit(runId, segmentId) { segment in
            segment.params.radiusStartMm = value
            segment.params.radiusEndMm = value
        }
    }

    private func setTurns(_ runId: String, _ segmentId: String, _ value: Double) {
        edit(runId, segmentId) { segment in
            segment.params.turns = value
        }
    }

    private func setOverride(_ runId: String, _ segmentId: String, _ value: Int) {
        edit(runId, segmentId) { segment in
            segment.pixelCountOverride = value == 0 ? nil : value
        }
    }

    private func edit(_ runId: String, _ segmentId: String, change: (inout Segment) -> Void) {
        guard var fixture, let runIndex = fixture.runs.firstIndex(where: { $0.id == runId }),
              let segmentIndex = fixture.runs[runIndex].segments.firstIndex(where: { $0.id == segmentId }) else { return }
        change(&fixture.runs[runIndex].segments[segmentIndex])
        draft = fixture
    }

    private func markReference() {
        guard var fixture, !fixture.waypoints.isEmpty else { return }
        let count = fixture.waypoints.filter { ($0.label ?? "").hasPrefix("Reference") }.count
        fixture.waypoints[fixture.waypoints.count - 1].label = count == 0 ? "Reference A" : "Reference B"
        draft = fixture
        Haptics.place()
    }

    private func spreadDrift() {
        guard var fixture, fixture.waypoints.count >= 2 else { return }
        let reference = fixture.waypoints.first { ($0.label ?? "").hasPrefix("Reference") } ?? fixture.waypoints[0]
        let error = fixture.waypoints[fixture.waypoints.count - 1].position - reference.position
        let moved = distributeDrift(fixture.waypoints.map(\.position), error: error)
        for index in fixture.waypoints.indices {
            fixture.waypoints[index].position = moved[index]
        }
        for runIndex in fixture.runs.indices {
            guard var corrections = fixture.runs[runIndex].pixelCorrections else { continue }
            for index in corrections.indices {
                let fraction = driftFraction(corrections[index].index, count: fixture.runs[runIndex].pixelCount)
                corrections[index].position = corrections[index].position - error * fraction
            }
            fixture.runs[runIndex].pixelCorrections = corrections
        }
        draft = fixture
        Haptics.success()
    }

    private func save() async {
        guard var fixture else { return }
        saving = true
        defer { saving = false }
        do {
            fixture = try await model.client.update(fixture)
            draft = fixture
            model.replace(fixture)
            Haptics.success()
        } catch {
            model.message = error.localizedDescription
            Haptics.warning()
        }
    }
}
