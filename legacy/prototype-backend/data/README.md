# data/ —— 模块A(数据与知识库)

此目录归**模块A**所有,其他模块 PR 不得改动。

模块A并入后应包含:

```
data/
├── raw/
│   ├── school/       # 校级文件(学籍管理规定、推免管理办法、培养方案指导意见)
│   ├── it/           # 计算机与人工智能学院
│   ├── <college>/    # 其他学院,目录名用学院拼音/缩写
│   └── scanned/      # 待OCR扫描件
├── sources.csv       # 来源登记表(file,doc_title,level,college,cohort,year,status,page_url,file_url,收集日期)
└── chunks.jsonl      # 切分产出,字段见主 README 契约1
```

命名规则:`层级_文件名_年份.扩展名`(中文即可,禁止用官网原始乱码文件名)。
