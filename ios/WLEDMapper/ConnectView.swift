import SwiftUI

struct ConnectView: View {
    @EnvironmentObject private var model: AppModel
    @FocusState private var focused: Bool

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("WLED Mapper")
                        .font(.largeTitle.weight(.semibold))
                        .foregroundStyle(Theme.ink)
                    Text("Point the phone at the lights.")
                        .font(.title3)
                        .foregroundStyle(Theme.muted)
                }
                .padding(.top, 24)

                if !model.servers.isEmpty {
                    VStack(spacing: 10) {
                        ForEach(model.servers) { server in
                            Button {
                                model.serverURL = server.url
                                Task { await model.connect() }
                            } label: {
                                HStack {
                                    Image(systemName: "dot.radiowaves.left.and.right")
                                    Text(server.url)
                                        .lineLimit(1)
                                    Spacer()
                                }
                                .font(.body.weight(.medium))
                                .foregroundStyle(Theme.ink)
                                .padding(16)
                                .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("Server")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(Theme.muted)
                    TextField("http://truenas:8787", text: $model.serverURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .textContentType(.URL)
                        .focused($focused)
                        .font(.title3)
                        .foregroundStyle(Theme.ink)
                        .padding(16)
                        .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                        .accessibilityIdentifier("server.url")
                }

                if model.needsPassword {
                    SecureField("Password", text: $model.password)
                        .textContentType(.password)
                        .font(.title3)
                        .foregroundStyle(Theme.ink)
                        .padding(16)
                        .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                }

                if let message = model.message {
                    Text(message)
                        .font(.callout)
                        .foregroundStyle(Theme.warn)
                }

                Spacer()

                BigButton(title: model.busy ? "Connecting" : "Continue") {
                    focused = false
                    Haptics.place()
                    Task { await model.connect() }
                }
                .disabled(model.busy)
            }
            .padding(24)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("screen.connect")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .navigationBar)
    }
}
