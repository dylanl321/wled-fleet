import MapperGeometry
import SwiftUI
import UIKit

enum Theme {
    static let background = Color(red: 0.07, green: 0.07, blue: 0.08)
    static let card = Color(red: 0.14, green: 0.14, blue: 0.16)
    static let ink = Color(red: 0.96, green: 0.94, blue: 0.90)
    static let muted = Color(red: 0.62, green: 0.60, blue: 0.55)
    static let amber = Color(red: 1.0, green: 0.74, blue: 0.32)
    static let good = Color(red: 0.49, green: 0.84, blue: 0.58)
    static let warn = Color(red: 1.0, green: 0.45, blue: 0.32)
}

enum Haptics {
    static func place() {
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
    }

    static func undo() {
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
    }

    static func success() {
        UINotificationFeedbackGenerator().notificationOccurred(.success)
    }

    static func warning() {
        UINotificationFeedbackGenerator().notificationOccurred(.warning)
    }
}

struct BigButton: View {
    var title: String
    var prominent = true
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.title3.weight(.semibold))
                .frame(maxWidth: .infinity)
                .frame(height: 56)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(prominent ? Color.black : Theme.ink)
        .background(prominent ? Theme.amber : Theme.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}

extension SegmentKind {
    var title: String {
        switch self {
        case .straight: return "Straight"
        case .polyline: return "Polyline"
        case .helix: return "Helix"
        case .curve: return "Curve"
        case .jump: return "Jump"
        }
    }
}

extension SurfaceHint {
    var title: String {
        switch self {
        case .planar: return "Wall"
        case .cylindrical: return "Column"
        case .branching: return "Tree"
        case .freeform: return "Freeform"
        }
    }
}
