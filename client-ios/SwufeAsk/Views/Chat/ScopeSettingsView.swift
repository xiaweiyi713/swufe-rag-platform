import SwiftUI

/// 提问范围设置：选择学院与入学年级。选项来自 `GET /options`，
/// 选定后随每次 `/ask` 请求发送，检索在排序前按范围过滤。
struct ScopeSettingsView: View {
    @Bindable var model: ChatViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                if let options = model.options {
                    Section("我的学院") {
                        Picker("学院", selection: Binding(
                            get: { model.college },
                            set: { model.selectCollege($0) }
                        )) {
                            Text("不限（全校）").tag(String?.none)
                            ForEach(options.colleges, id: \.self) { college in
                                Text(college).tag(String?.some(college))
                            }
                        }
                    }
                    Section("入学年级") {
                        Picker("年级", selection: Binding(
                            get: { model.cohort },
                            set: { model.selectCohort($0) }
                        )) {
                            Text("不限").tag(String?.none)
                            ForEach(options.cohorts, id: \.self) { cohort in
                                Text("\(cohort)级").tag(String?.some(cohort))
                            }
                        }
                    }
                    let majors = options.majors(
                        for: model.cohort,
                        college: model.college
                    )
                    Section {
                        if majors.isEmpty {
                            Text("当前学院与年级暂无专业选项")
                                .foregroundStyle(.secondary)
                        } else {
                            Picker("专业", selection: Binding(
                                get: { model.major },
                                set: { model.selectMajor($0) }
                            )) {
                                Text("不限").tag(String?.none)
                                ForEach(majors, id: \.self) { major in
                                    Text(major).tag(String?.some(major))
                                }
                            }
                            .pickerStyle(.navigationLink)
                        }
                    } header: {
                        Text("我的专业")
                    } footer: {
                        Text("专业列表会按学院和年级筛选；先选专业时，学院会自动匹配专业归属。选定后，检索只允许匹配范围的现行文件参与排序。")
                    }
                } else if model.isOptionsLoading {
                    Section {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text("正在读取学院与年级选项…")
                                .foregroundStyle(.secondary)
                        }
                    }
                } else {
                    Section {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(model.optionsError ?? "还没有连接到教务问答后端。")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                            Button("重新加载") {
                                model.reloadOptions()
                            }
                        }
                    }
                }
            }
            .navigationTitle("提问范围")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
            }
            .onAppear {
                model.loadOptionsIfNeeded()
                model.reconcileScopeSelection()
            }
        }
    }
}
