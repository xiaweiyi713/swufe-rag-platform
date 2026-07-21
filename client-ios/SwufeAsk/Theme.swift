import SwiftUI

/// User-selectable appearance, persisted via `@AppStorage` and applied with
/// `.preferredColorScheme` at the app root.
enum AppearanceMode: String, CaseIterable, Identifiable {
    case system
    case light
    case dark

    /// 统一的 `@AppStorage` 键，App 入口与侧栏共用。
    static let storageKey = "swufeask.appearance"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .system: "跟随系统"
        case .light: "浅色"
        case .dark: "深色"
        }
    }

    var symbol: String {
        switch self {
        case .system: "iphone"
        case .light: "sun.max"
        case .dark: "moon.stars"
        }
    }

    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }
}

/// App-wide design system: spacing, radius, color, typography, motion, and the
/// reusable "Liquid Glass" surface treatment.
///
/// Dark-mode-first: colors are tuned to glow on a deep canvas while still
/// adapting cleanly in Light mode through system materials and hierarchical
/// styles. Targeting iOS 17, the glass look is built from `ultraThinMaterial`
/// plus a restrained highlight stroke, while iOS 26 uses native
/// `.glassEffect()` where available.
enum Theme {
    enum Spacing {
        static let xxs: CGFloat = 4
        static let xs: CGFloat = 8
        static let sm: CGFloat = 12
        static let md: CGFloat = 16
        static let lg: CGFloat = 24
        static let xl: CGFloat = 32
    }

    enum Radius {
        static let sm: CGFloat = 14
        static let md: CGFloat = 20
        static let lg: CGFloat = 28
        static let pill: CGFloat = 999
    }

    enum Color {
        // Brand — SWUFE badge blue (#0068B7) in Light mode, monochrome white in
        // Dark mode. `onAccent` is the color placed on top of an accent-filled
        // surface so labels stay legible in both schemes (white on blue in
        // Light, black on white in Dark).
        static let accent = SwiftUI.Color(UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? .white
                : UIColor(red: 0.0, green: 0.408, blue: 0.718, alpha: 1)
        })
        static let accentSoft = accent.opacity(0.72)
        static let onAccent = SwiftUI.Color(.systemBackground)
        static let actionBlue = SwiftUI.Color(red: 0.0, green: 0.36, blue: 0.68)
        static let actionBlueGlass = actionBlue.opacity(0.70)

        // Surfaces (names kept for backwards compatibility with existing views)
        static let cardBackground = SwiftUI.Color(.secondarySystemBackground)
        static let cardStroke = SwiftUI.Color.white.opacity(0.10)
        static let quietText = SwiftUI.Color.secondary

        // Glass detailing
        static let glassHighlight = SwiftUI.Color.white.opacity(0.18)
        static let glassStroke = SwiftUI.Color.white.opacity(0.20)

        // App canvases are deliberately flat colors. Light mode is a nearly
        // white cool blue; dark mode is true black.
        static let lightCanvas = SwiftUI.Color(red: 0.965, green: 0.978, blue: 0.995)
        static let darkCanvas = SwiftUI.Color.black
    }

    enum Motion {
        static let spring = Animation.spring(response: 0.42, dampingFraction: 0.82)
        static let snappy = Animation.snappy(duration: 0.28)
        static let gentle = Animation.easeInOut(duration: 0.45)
    }

}

/// Circular SWUFE crest used as the assistant and app identity mark.
struct SwufeLogoMark: View {
    let size: CGFloat

    init(size: CGFloat = 32) {
        self.size = size
    }

    var body: some View {
        Image("SwufeLogo")
            .resizable()
            .scaledToFit()
            .padding(size * 0.045)
            .frame(width: size, height: size)
            .background(SwiftUI.Color.white, in: Circle())
            .clipShape(Circle())
            .overlay {
                Circle()
                    .stroke(SwiftUI.Color.white.opacity(0.8), lineWidth: 0.5)
            }
            .accessibilityHidden(true)
    }
}

// MARK: - Liquid Glass surface

/// A translucent, frosted "Liquid Glass" surface with a light-catching edge.
struct LiquidGlass: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme
    var radius: CGFloat = Theme.Radius.md
    var elevated: Bool = true

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(iOS 26.0, *) {
            if colorScheme == .dark {
                content
                    .glassEffect(
                        .regular.tint(Color.black.opacity(0.72)),
                        in: .rect(cornerRadius: radius)
                    )
            } else {
                content
                    .glassEffect(.regular, in: .rect(cornerRadius: radius))
            }
        } else {
            content
                .background(.ultraThinMaterial, in: .rect(cornerRadius: radius))
                .overlay {
                    RoundedRectangle(cornerRadius: radius)
                        .strokeBorder(Theme.Color.glassStroke, lineWidth: 1)
                }
        }
    }
}

struct CleanGlassCapsuleSurface: ViewModifier {
    let interactive: Bool

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(iOS 26.0, *) {
            if interactive {
                content.glassEffect(.regular.interactive(), in: Capsule())
            } else {
                content.glassEffect(.regular, in: Capsule())
            }
        } else {
            content
                .background(.ultraThinMaterial, in: Capsule())
                .overlay {
                    Capsule().strokeBorder(Theme.Color.glassStroke, lineWidth: 1)
                }
        }
    }
}

struct CleanGlassCircleSurface: ViewModifier {
    let interactive: Bool

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(iOS 26.0, *) {
            if interactive {
                content.glassEffect(.regular.interactive(), in: Circle())
            } else {
                content.glassEffect(.regular, in: Circle())
            }
        } else {
            content
                .background(.ultraThinMaterial, in: Circle())
                .overlay {
                    Circle().strokeBorder(Theme.Color.glassStroke, lineWidth: 1)
                }
        }
    }
}

struct CleanGlassRoundedSurface: ViewModifier {
    let radius: CGFloat
    let interactive: Bool

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(iOS 26.0, *) {
            if interactive {
                content.glassEffect(
                    .regular.interactive(),
                    in: .rect(cornerRadius: radius)
                )
            } else {
                content.glassEffect(.regular, in: .rect(cornerRadius: radius))
            }
        } else {
            content
                .background(.ultraThinMaterial, in: .rect(cornerRadius: radius))
                .overlay {
                    RoundedRectangle(cornerRadius: radius)
                        .strokeBorder(Theme.Color.glassStroke, lineWidth: 1)
                }
        }
    }
}

struct ActionBlueCapsuleSurface: ViewModifier {
    let isActive: Bool

    @ViewBuilder
    func body(content: Content) -> some View {
        if isActive {
            if #available(iOS 26.0, *) {
                content
                    .glassEffect(
                        .regular.tint(Theme.Color.actionBlue).interactive(),
                        in: Capsule()
                    )
            } else {
                content
                    .background(Theme.Color.actionBlueGlass, in: Capsule())
                    .background(.ultraThinMaterial, in: Capsule())
                    .overlay {
                        Capsule()
                            .strokeBorder(Color.white.opacity(0.34), lineWidth: 1)
                    }
            }
        } else {
            content.modifier(CleanGlassCapsuleSurface(interactive: true))
        }
    }
}

struct ActionBlueBubbleSurface: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme
    let radius: CGFloat

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(iOS 26.0, *) {
            if colorScheme == .dark {
                content
                    .glassEffect(
                        .regular.tint(Color.white.opacity(0.10)),
                        in: .rect(cornerRadius: radius)
                    )
            } else {
                content
                    .glassEffect(
                        .regular.tint(Theme.Color.actionBlue),
                        in: .rect(cornerRadius: radius)
                    )
            }
        } else {
            content
                .background(
                    colorScheme == .dark
                        ? Color.white.opacity(0.10)
                        : Theme.Color.actionBlueGlass,
                    in: .rect(cornerRadius: radius)
                )
                .background(.ultraThinMaterial, in: .rect(cornerRadius: radius))
                .overlay {
                    RoundedRectangle(cornerRadius: radius)
                        .strokeBorder(
                            Color.white.opacity(colorScheme == .dark ? 0.16 : 0.28),
                            lineWidth: 1
                        )
                }
        }
    }
}

extension View {
    /// Frosted Liquid Glass card surface.
    func liquidGlass(radius: CGFloat = Theme.Radius.md, elevated: Bool = true) -> some View {
        modifier(LiquidGlass(radius: radius, elevated: elevated))
    }

    /// Backwards-compatible alias used by existing views; now renders as glass so
    /// untouched screens inherit the new look automatically.
    func cardSurface(radius: CGFloat = Theme.Radius.lg) -> some View {
        liquidGlass(radius: radius, elevated: true)
    }

    func actionBlueGlassCapsule(isActive: Bool = true) -> some View {
        modifier(ActionBlueCapsuleSurface(isActive: isActive))
    }

    func actionBlueGlassBubble(radius: CGFloat = 20) -> some View {
        modifier(ActionBlueBubbleSurface(radius: radius))
    }

    func cleanGlassCapsule(interactive: Bool = false) -> some View {
        modifier(CleanGlassCapsuleSurface(interactive: interactive))
    }

    func cleanGlassCircle(interactive: Bool = false) -> some View {
        modifier(CleanGlassCircleSurface(interactive: interactive))
    }

    func cleanGlassRounded(
        radius: CGFloat,
        interactive: Bool = false
    ) -> some View {
        modifier(CleanGlassRoundedSurface(radius: radius, interactive: interactive))
    }
}

// MARK: - Deep canvas background

/// Full-screen glass canvas for every app surface, including safe areas.
/// Dark and light appearances use different palettes but keep the same layout
/// treatment so the UI does not split into unrelated backgrounds.
/// 外观模式由 App 根部的 `.preferredColorScheme` 统一注入环境，这里直接读取。
struct LiquidBackdrop: View {
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        (colorScheme == .dark ? Theme.Color.darkCanvas : Theme.Color.lightCanvas)
            .ignoresSafeArea()
    }
}

// MARK: - Reusable glass tag

/// Small frosted pill used for floating labels, source metadata, and tag clouds.
struct GlassTag: View {
    let text: String
    var systemImage: String?
    var tint: Color = Theme.Color.accent
    var prominent = false

    var body: some View {
        label
            .font(.caption.weight(.semibold))
            .foregroundStyle(prominent ? AnyShapeStyle(Theme.Color.onAccent) : AnyShapeStyle(tint))
            .padding(.horizontal, Theme.Spacing.sm)
            .padding(.vertical, Theme.Spacing.xxs + 2)
            .background {
                if prominent {
                    Capsule().fill(tint)
                } else {
                    Capsule().fill(.ultraThinMaterial)
                    Capsule().fill(tint.opacity(0.16))
                    Capsule().strokeBorder(tint.opacity(0.35), lineWidth: 0.8)
                }
            }
    }

    @ViewBuilder private var label: some View {
        if let systemImage {
            Label(text, systemImage: systemImage)
        } else {
            Text(text)
        }
    }
}
