import SwiftUI

/// 聊天页顶部悬浮栏：侧栏入口、朗读开关（长按进语音设置）、提问范围菜单。
struct ChatTopBar: View {
    @Bindable var model: ChatViewModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var feedbackTrigger = 0
    @State private var isScopePressed = false

    let isSpeechOn: Bool
    let openSidebar: () -> Void
    let toggleSpeech: () -> Void
    let openVoiceSettings: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            HStack(spacing: 10) {
                Button {
                    perform(openSidebar)
                } label: {
                    topGlyph("sidebar.leading")
                }
                .buttonStyle(TopBarPressStyle(pressedScale: 0.91))
                .accessibilityLabel("菜单与历史")
                Button {
                    perform(toggleSpeech)
                } label: {
                    topGlyph(isSpeechOn ? "speaker.wave.2.fill" : "speaker.slash")
                        .contentTransition(.symbolEffect(.replace))
                        .symbolEffect(.bounce, value: isSpeechOn)
                }
                .buttonStyle(TopBarPressStyle(pressedScale: 0.91))
                .accessibilityLabel(isSpeechOn ? "关闭回复朗读" : "开启回复朗读")
                .simultaneousGesture(LongPressGesture(minimumDuration: 0.4).onEnded { _ in openVoiceSettings() })
                .contextMenu {
                    Button { openVoiceSettings() } label: {
                        Label("语音设置", systemImage: "slider.horizontal.3")
                    }
                }
            }
            Spacer()
            scopeControl
        }
        .padding(.horizontal, 14)
        .padding(.top, 6)
        .sensoryFeedback(
            .impact(weight: .light, intensity: 0.72),
            trigger: feedbackTrigger
        )
    }

    private var scopeControl: some View {
        ZStack {
            HStack(spacing: 6) {
                Image(systemName: "person.crop.rectangle")
                    .font(.system(size: 14, weight: .semibold))
                Text(model.scopeSummary)
                    .font(.footnote.weight(.semibold))
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            .foregroundStyle(.primary)
            .padding(.horizontal, 14)
            .frame(width: 200, height: 44)
            .modifier(TopCapsuleButtonSurface())
            .contentShape(.capsule)
            .accessibilityHidden(true)

            PersistentScopeMenuButton(model: model) { pressed in
                if pressed {
                    feedbackTrigger &+= 1
                    model.loadOptionsIfNeeded()
                }
                isScopePressed = pressed
            }
            .frame(width: 200, height: 44)
        }
        .scaleEffect(isScopePressed && !reduceMotion ? 0.97 : 1)
        .opacity(isScopePressed ? 0.78 : 1)
        .animation(
            reduceMotion
                ? .easeOut(duration: 0.08)
                : .spring(response: 0.24, dampingFraction: 0.66),
            value: isScopePressed
        )
    }

    private func topGlyph(_ name: String) -> some View {
        Image(systemName: name)
            .font(.system(size: 18, weight: .semibold))
            .foregroundStyle(.primary)
            .frame(width: 44, height: 44)
            .modifier(TopCircleButtonSurface())
            .contentShape(.rect)
    }

    private func perform(_ action: () -> Void) {
        feedbackTrigger &+= 1
        action()
    }
}

@MainActor
private struct PersistentScopeMenuButton: UIViewRepresentable {
    let model: ChatViewModel
    let setPressed: (Bool) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(setPressed: setPressed)
    }

    func makeUIView(context: Context) -> UIButton {
        let button = UIButton(type: .custom)
        button.backgroundColor = .clear
        button.showsMenuAsPrimaryAction = true
        button.preferredMenuElementOrder = .fixed
        context.coordinator.button = button
        configure(context.coordinator)
        button.menu = rootMenu(coordinator: context.coordinator)
        button.addTarget(context.coordinator, action: #selector(Coordinator.touchDown), for: .touchDown)
        button.addTarget(
            context.coordinator,
            action: #selector(Coordinator.touchEnded),
            for: [.touchUpInside, .touchUpOutside, .touchCancel, .touchDragExit]
        )
        return button
    }

    func updateUIView(_ button: UIButton, context: Context) {
        context.coordinator.setPressed = setPressed
        configure(context.coordinator)
        button.accessibilityLabel = "提问范围：\(model.scopeSummary)"
        button.accessibilityHint = "选择学院、入学年级和专业"
    }

    private func configure(_ coordinator: Coordinator) {
        coordinator.elementsForPage = { page in
            elements(for: page, coordinator: coordinator)
        }
    }

    private func rootMenu(coordinator: Coordinator) -> UIMenu {
        UIMenu(children: [
            UIDeferredMenuElement.uncached { completion in
                Task { @MainActor in
                    completion(elements(for: .root, coordinator: coordinator))
                }
            }
        ])
    }

    private func elements(for page: Page, coordinator: Coordinator) -> [UIMenuElement] {
        guard let options = model.options else {
            if model.isOptionsLoading {
                return [UIAction(title: "正在读取学院与专业…", attributes: .disabled) { _ in }]
            }
            return [
                UIAction(title: "重新加载范围选项", image: UIImage(systemName: "arrow.clockwise")) { _ in
                    MainActor.assumeIsolated {
                        model.reloadOptions()
                    }
                }
            ]
        }

        switch page {
        case .root:
            return rootElements(coordinator: coordinator)
        case .college:
            return choiceElements(
                title: model.college ?? "不限（全校）",
                icon: "building.columns",
                choices: [ScopeMenuChoice(value: nil, label: "不限（全校）")]
                    + options.colleges.map { ScopeMenuChoice(value: $0, label: $0) },
                selected: model.college,
                coordinator: coordinator,
                select: model.selectCollege
            )
        case .cohort:
            return choiceElements(
                title: model.cohort.map { "\($0)级" } ?? "不限年级",
                icon: "calendar",
                choices: [ScopeMenuChoice(value: nil, label: "不限年级")]
                    + options.cohorts.map { ScopeMenuChoice(value: $0, label: "\($0)级") },
                selected: model.cohort,
                coordinator: coordinator,
                select: model.selectCohort
            )
        case .major:
            return choiceElements(
                title: model.major ?? "不限专业",
                icon: "graduationcap",
                choices: [ScopeMenuChoice(value: nil, label: "不限专业")]
                    + options.majors(for: model.cohort, college: model.college).map {
                        ScopeMenuChoice(value: $0, label: $0)
                    },
                selected: model.major,
                coordinator: coordinator,
                select: model.selectMajor
            )
        }
    }

    private func rootElements(coordinator: Coordinator) -> [UIMenuElement] {
        let college = navigationAction(
            title: model.college ?? "不限（全校）",
            icon: "building.columns",
            page: .college,
            coordinator: coordinator
        )
        let cohort = navigationAction(
            title: model.cohort.map { "\($0)级" } ?? "不限年级",
            icon: "calendar",
            page: .cohort,
            coordinator: coordinator
        )
        let major = navigationAction(
            title: model.major ?? "不限专业",
            icon: "graduationcap",
            page: .major,
            coordinator: coordinator
        )

        var resetAttributes: UIMenuElement.Attributes = [.keepsMenuPresented]
        if model.college == nil && model.cohort == nil && model.major == nil {
            resetAttributes.insert(.disabled)
        }
        let reset = UIAction(
            title: "重置为全校不限",
            image: UIImage(systemName: "arrow.counterclockwise"),
            attributes: resetAttributes
        ) { _ in
            MainActor.assumeIsolated {
                model.selectMajor(nil)
                model.selectCollege(nil)
                model.selectCohort(nil)
                coordinator.show(.root)
            }
        }
        return [college, cohort, major, reset]
    }

    private func navigationAction(
        title: String,
        icon: String,
        page: Page,
        coordinator: Coordinator
    ) -> UIAction {
        UIAction(
            title: title,
            image: UIImage(systemName: icon),
            attributes: [.keepsMenuPresented]
        ) { _ in
            MainActor.assumeIsolated {
                coordinator.show(page)
            }
        }
    }

    private func choiceElements(
        title: String,
        icon: String,
        choices: [ScopeMenuChoice],
        selected: String?,
        coordinator: Coordinator,
        select: @escaping @MainActor (String?) -> Void
    ) -> [UIMenuElement] {
        let back = UIAction(
            title: title,
            image: UIImage(systemName: icon),
            attributes: [.keepsMenuPresented]
        ) { _ in
            MainActor.assumeIsolated {
                coordinator.show(.root)
            }
        }
        back.subtitle = "返回提问范围"

        let actions = choices.map { choice in
            UIAction(
                title: choice.label,
                attributes: [.keepsMenuPresented],
                state: choice.value == selected ? .on : .off
            ) { _ in
                MainActor.assumeIsolated {
                    select(choice.value)
                    coordinator.show(.root)
                }
            }
        }
        let choicesMenu = UIMenu(options: [.displayInline, .singleSelection], children: actions)
        return [back, choicesMenu]
    }

    fileprivate enum Page {
        case root
        case college
        case cohort
        case major
    }

    final class Coordinator: NSObject {
        weak var button: UIButton?
        var setPressed: (Bool) -> Void
        var elementsForPage: ((Page) -> [UIMenuElement])?

        init(setPressed: @escaping (Bool) -> Void) {
            self.setPressed = setPressed
        }

        @objc func touchDown() {
            setPressed(true)
        }

        @objc func touchEnded() {
            setPressed(false)
        }

        @MainActor
        func show(_ page: Page) {
            guard let elements = elementsForPage?(page),
                  let interaction = button?.interactions.first(where: {
                      $0 is UIContextMenuInteraction
                  }) as? UIContextMenuInteraction else {
                return
            }
            interaction.updateVisibleMenu { visibleMenu in
                visibleMenu.replacingChildren(elements)
            }
        }
    }
}

private struct ScopeMenuChoice {
    let value: String?
    let label: String
}

/// ChatGPT-style tactile press: compress while the finger is down, then return
/// with a short spring. Reduce Motion keeps the highlight feedback without the
/// spatial movement.
private struct TopBarPressStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let pressedScale: CGFloat

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(
                configuration.isPressed && !reduceMotion ? pressedScale : 1
            )
            .opacity(configuration.isPressed ? 0.78 : 1)
            .brightness(configuration.isPressed ? -0.035 : 0)
            .animation(
                reduceMotion
                    ? .easeOut(duration: 0.08)
                    : .spring(response: 0.24, dampingFraction: 0.66),
                value: configuration.isPressed
            )
    }
}

struct TopCircleButtonSurface: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme

    func body(content: Content) -> some View {
        content
            .background(.ultraThinMaterial, in: .circle)
            .background(
                Circle()
                    .fill(colorScheme == .light ? Color.clear : Color.white.opacity(0.12))
            )
            .overlay {
                Circle()
                    .strokeBorder(Theme.Gradient.glassStroke, lineWidth: 1)
            }
            .shadow(color: .black.opacity(0.16), radius: 10, y: 5)
    }
}

struct TopCapsuleButtonSurface: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme

    func body(content: Content) -> some View {
        content
            .background(.ultraThinMaterial, in: .capsule)
            .background(
                Capsule()
                    .fill(colorScheme == .light ? Color.clear : Color.white.opacity(0.12))
            )
            .overlay {
                Capsule()
                    .strokeBorder(Theme.Gradient.glassStroke, lineWidth: 1)
            }
            .shadow(color: .black.opacity(0.16), radius: 10, y: 5)
    }
}
