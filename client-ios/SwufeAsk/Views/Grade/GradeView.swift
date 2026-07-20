import SwiftUI
import WebKit

/// A first-party browser surface for the university's own WebVPN flow.
/// Credentials and authenticated page data stay inside WebKit; the RAG API is
/// deliberately not involved in this session.
struct GradeView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var session = GradeWebSession()
    @State private var showsClearSessionAlert = false

    var body: some View {
        NavigationStack {
            GradeWebView(session: session)
                .ignoresSafeArea(edges: .bottom)
                .navigationTitle("我的成绩")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItemGroup(placement: .topBarLeading) {
                        Button("后退", systemImage: "chevron.left") {
                            session.goBack()
                        }
                        .labelStyle(.iconOnly)
                        .disabled(!session.canGoBack)
                        .help("后退")

                        Button("前进", systemImage: "chevron.right") {
                            session.goForward()
                        }
                        .labelStyle(.iconOnly)
                        .disabled(!session.canGoForward)
                        .help("前进")
                    }

                    ToolbarItemGroup(placement: .topBarTrailing) {
                        Button("刷新", systemImage: "arrow.clockwise") {
                            session.reload()
                        }
                        .labelStyle(.iconOnly)
                        .help("刷新")

                        Menu("会话", systemImage: "ellipsis.circle") {
                            Button("清除登录会话", systemImage: "trash", role: .destructive) {
                                showsClearSessionAlert = true
                            }
                        }
                        .labelStyle(.iconOnly)

                        Button("完成") {
                            dismiss()
                        }
                    }
                }
                .safeAreaInset(edge: .top, spacing: 0) {
                    if session.isLoading {
                        ProgressView(value: session.progress)
                            .progressViewStyle(.linear)
                            .tint(Theme.Color.accent)
                            .frame(height: 2)
                            .background(.clear)
                    }
                }
                .alert("清除登录会话？", isPresented: $showsClearSessionAlert) {
                    Button("清除", role: .destructive) {
                        session.clearSession()
                    }
                    Button("取消", role: .cancel) {}
                } message: {
                    Text("下次打开时需要重新登录学校 WebVPN。")
                }
        }
    }
}

private struct GradeWebView: UIViewRepresentable {
    @ObservedObject var session: GradeWebSession

    func makeUIView(context: Context) -> WKWebView {
        session.webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}
}

/// Owns one persistent, app-local WebKit session. The app never reads cookies,
/// form values, or page bodies; it only observes navigation state for controls.
@MainActor
private final class GradeWebSession: NSObject, ObservableObject {
    static let webVPNURL = URL(string: "https://webvpn.swufe.edu.cn/")!

    let webView: WKWebView

    @Published private(set) var progress = 0.0
    @Published private(set) var isLoading = false
    @Published private(set) var canGoBack = false
    @Published private(set) var canGoForward = false

    private var progressObservation: NSKeyValueObservation?
    private var navigationObservation: NSKeyValueObservation?

    override init() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.allowsBackForwardNavigationGestures = true
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        self.webView = webView

        super.init()

        webView.navigationDelegate = self
        webView.uiDelegate = self
        progressObservation = webView.observe(\.estimatedProgress, options: [.initial, .new]) { [weak self] view, _ in
            Task { @MainActor in
                self?.progress = view.estimatedProgress
            }
        }
        navigationObservation = webView.observe(\.canGoBack, options: [.initial, .new]) { [weak self] view, _ in
            Task { @MainActor in
                self?.canGoBack = view.canGoBack
                self?.canGoForward = view.canGoForward
            }
        }

        loadHome()
    }

    deinit {
        progressObservation?.invalidate()
        navigationObservation?.invalidate()
    }

    func loadHome() {
        webView.load(URLRequest(url: Self.webVPNURL,
                                cachePolicy: .useProtocolCachePolicy,
                                timeoutInterval: 30))
    }

    func reload() {
        webView.reload()
    }

    func goBack() {
        guard webView.canGoBack else { return }
        webView.goBack()
    }

    func goForward() {
        guard webView.canGoForward else { return }
        webView.goForward()
    }

    func clearSession() {
        let allTypes = WKWebsiteDataStore.allWebsiteDataTypes()
        WKWebsiteDataStore.default().removeData(ofTypes: allTypes,
                                                 modifiedSince: .distantPast) { [weak self] in
            Task { @MainActor in
                self?.loadHome()
            }
        }
    }
}

extension GradeWebSession: WKNavigationDelegate, WKUIDelegate {
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        isLoading = true
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        canGoBack = webView.canGoBack
        canGoForward = webView.canGoForward
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        isLoading = false
        progress = 1
        canGoBack = webView.canGoBack
        canGoForward = webView.canGoForward
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        isLoading = false
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        isLoading = false
    }

    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }

        // The CAS flow and WebVPN may use several official subdomains. Keeping
        // navigation on the university domain prevents an accidental handoff
        // from turning this authenticated surface into a general browser.
        let isOfficialHost = url.host?.lowercased() == "swufe.edu.cn"
            || url.host?.lowercased().hasSuffix(".swufe.edu.cn") == true
        let isAllowedScheme = url.scheme == "https"

        guard isOfficialHost && isAllowedScheme else {
            decisionHandler(.cancel)
            return
        }

        // Links opened as a new window (common in CAS and WebVPN portals) stay
        // in the same controlled WebView instead of becoming an unmanaged tab.
        if navigationAction.targetFrame == nil {
            webView.load(navigationAction.request)
            decisionHandler(.cancel)
            return
        }

        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView,
                 createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard let url = navigationAction.request.url,
              url.scheme == "https",
              url.host?.lowercased().hasSuffix(".swufe.edu.cn") == true || url.host?.lowercased() == "swufe.edu.cn" else {
            return nil
        }
        webView.load(navigationAction.request)
        return nil
    }
}
