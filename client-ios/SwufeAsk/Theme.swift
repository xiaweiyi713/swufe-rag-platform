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
/// plus a highlight stroke and soft shadow (a faithful stand-in for the native
/// iOS 26 `.glassEffect()`), so it compiles and runs on the project's target.
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

        // Surfaces (names kept for backwards compatibility with existing views)
        static let cardBackground = SwiftUI.Color(.secondarySystemBackground)
        static let cardStroke = SwiftUI.Color.white.opacity(0.10)
        static let quietText = SwiftUI.Color.secondary

        // Glass detailing
        static let glassHighlight = SwiftUI.Color.white.opacity(0.18)

        static let darkCanvasTop = SwiftUI.Color(red: 0.115, green: 0.12, blue: 0.12)
        static let darkCanvasMid = SwiftUI.Color(red: 0.12, green: 0.13, blue: 0.13)
        static let darkCanvasBottom = SwiftUI.Color(red: 0.02, green: 0.025, blue: 0.03)
        // Light canvas leans into the badge blue: near-white with a cold cast
        // at the top, settling into a soft blue wash at the bottom.
        static let lightCanvasTop = SwiftUI.Color(red: 0.94, green: 0.96, blue: 0.99)
        static let lightCanvasMid = SwiftUI.Color(red: 0.86, green: 0.91, blue: 0.97)
        static let lightCanvasBottom = SwiftUI.Color(red: 0.71, green: 0.80, blue: 0.91)
    }

    enum Motion {
        static let spring = Animation.spring(response: 0.42, dampingFraction: 0.82)
        static let snappy = Animation.snappy(duration: 0.28)
        static let gentle = Animation.easeInOut(duration: 0.45)
    }

    enum Gradient {
        static var brand: LinearGradient {
            LinearGradient(
                colors: [Theme.Color.accent, Theme.Color.accentSoft],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }

        /// Diagonal top-left highlight that reads as light catching a glass edge.
        static var glassStroke: LinearGradient {
            LinearGradient(
                colors: [
                    Theme.Color.glassHighlight,
                    Theme.Color.glassHighlight.opacity(0.25),
                    .clear
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }
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

/// A translucent, frosted "Liquid Glass" surface with a light-catching edge and
/// depth shadow. `elevated` controls how much the card appears to float.
struct LiquidGlass: ViewModifier {
    var radius: CGFloat = Theme.Radius.md
    var elevated: Bool = true

    func body(content: Content) -> some View {
        content
            .background(.ultraThinMaterial, in: .rect(cornerRadius: radius))
            .overlay {
                RoundedRectangle(cornerRadius: radius)
                    .strokeBorder(Theme.Gradient.glassStroke, lineWidth: 1)
            }
            .shadow(color: .black.opacity(elevated ? 0.32 : 0.16),
                    radius: elevated ? 22 : 10,
                    y: elevated ? 12 : 5)
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
}

// MARK: - Deep canvas background

/// Full-screen glass canvas for every app surface, including safe areas.
/// Dark and light appearances use different palettes but keep the same layout
/// treatment so the UI does not split into unrelated backgrounds.
/// 外观模式由 App 根部的 `.preferredColorScheme` 统一注入环境，这里直接读取。
struct LiquidBackdrop: View {
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ZStack {
            LinearGradient(
                colors: canvasColors,
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            LinearGradient(
                colors: [
                    .white.opacity(colorScheme == .dark ? 0.18 : 0.40),
                    .white.opacity(colorScheme == .dark ? 0.04 : 0.16),
                    .clear
                ],
                startPoint: .topLeading,
                endPoint: .center
            )

            LinearGradient(
                colors: [
                    .clear,
                    .black.opacity(colorScheme == .dark ? 0.28 : 0.10)
                ],
                startPoint: .center,
                endPoint: .bottom
            )
        }
        .ignoresSafeArea()
        .drawingGroup(opaque: true, colorMode: .linear)
    }

    private var canvasColors: [Color] {
        if colorScheme == .dark {
            [
                Theme.Color.darkCanvasTop,
                Theme.Color.darkCanvasMid,
                Theme.Color.darkCanvasBottom
            ]
        } else {
            [
                Theme.Color.lightCanvasTop,
                Theme.Color.lightCanvasMid,
                Theme.Color.lightCanvasBottom
            ]
        }
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
                    Capsule().fill(tint.gradient)
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
