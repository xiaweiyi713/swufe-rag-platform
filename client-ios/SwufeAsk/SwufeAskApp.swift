import SwiftData
import SwiftUI
import UserNotifications

@main
struct SwufeAskApp: App {
    @State private var chatModel = ChatViewModel()
    @AppStorage(AppearanceMode.storageKey) private var appearanceRaw = AppearanceMode.dark.rawValue
    @Environment(\.scenePhase) private var scenePhase

    private let container: ModelContainer
    /// 让上课提醒在 App 前台时也能弹横幅。
    private let notificationPresenter = NotificationForegroundPresenter()

    init() {
        do {
            container = try ModelContainer(for: StoredConversation.self, CourseEntry.self)
        } catch {
            fatalError("无法初始化本地数据库:\(error.localizedDescription)")
        }
        UNUserNotificationCenter.current().delegate = notificationPresenter
    }

    private var appearance: AppearanceMode {
        AppearanceMode(rawValue: appearanceRaw) ?? .system
    }

    var body: some Scene {
        WindowGroup {
            ChatView(model: chatModel)
                .preferredColorScheme(appearance.colorScheme)
        }
        .modelContainer(container)
        .onChange(of: scenePhase) { _, phase in
            // 提醒按未来 7 天滚动排,每次回前台续期一次。
            if phase == .active {
                CourseReminderScheduler.refresh(using: container.mainContext)
            }
        }
    }
}
