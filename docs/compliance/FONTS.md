# 自托管字体与多语言导出

仅分发 OFL 开源字体，不打包或嵌入系统字体，不使用字体 CDN。

浏览器使用 Fontsource Inter 5.3.0、Noto Sans SC 5.3.1、Noto Naskh Arabic 5.3.0 的 400/600 WOFF2。上游已经按 unicode-range 分包，本项目不再修改字体字节。完整文件 SHA-256、版本和分包性质见 [browser-fonts-manifest.json](browser-fonts-manifest.json)。CSS 使用 font-display: swap，浏览器按字符范围按需加载。保留各包 OFL 全文，许可页可查看。

PDF 使用 Google Fonts 官方仓库固定提交 `e7db6ca603fd4862010383ff17ffd6cc4ffac1c0` 的未修改 TTF。来源、哈希与许可原件保存在 `backend/app/analysis/fonts/open-fonts-manifest.json`，启动导出前校验文件哈希。

| 字体 | 字节数 | SHA-256 |
|---|---:|---|
| notosanssc | 17772300 | `a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da` |
| notonaskharabic | 307592 | `67b5a525a661b607971fbd3f96a81b89d3a768e74534fca84f18ac97e6fab72f` |
| notosanskr | 10414588 | `194018e6b2b293a7964f037b25c0249ce1418bc9ab3c971060a03aa57861e252` |
| notosansthai | 218652 | `5a1c559bb539583c8a1fd99d1c5b9491e5e14478c9cd2bd0970d5c3096cc9ef8` |
| notosansbengali | 463668 | `dcd42978094e584a849c84a51450eeac40c8826057d566ea6d4b9627a403a05a` |
| notosanshebrew | 112640 | `7ef36a2c3593758cdb622e1bdef4f84523e92fbc3ccc667438dd80ff54c2de88` |
| inter | 876576 | `29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031` |

PDF 由 WeasyPrint 70 + Pango/HarfBuzz 进行多文字整形、阿拉伯语/希伯来语从右到左排版、泰语无空格安全断行和字体子集化嵌入；PDF 内部自动子集化，不把生成的子集字体作为独立字体产品发布。为保障 17 种官方语言（中文、英文及法、德、西、葡、意、荷、印尼、土、波、日、韩、泰、越、阿、俄 15 种属地国官方语言）导出在 2GB 轻量云主机上零交换分区稳定运行，`pdf_renderer.py` 按当前文档实际出现的 Unicode 文字脚本**动态按需加载**上述 7 套随包校验开源字体（例如纯拉丁/西里尔文档仅加载 `Inter`，日语汉字与平片假名由 `Noto Sans SC` 完整覆盖并通过日语汉字词汇与假名双特征门禁，韩/泰/阿/孟加拉/希伯来各自按脚本按需挂载），并在配合 `CYSH_MAX_CONCURRENT_PDF_RENDERS` 渲染信号量完成输出后及时释放内存峰值。所选字体的 OFL 声明中，Noto Sans SC 的保留名称是 Source，Noto/Inter 的所用名称未列作保留名称；版权和 OFL 全文随包保留。Word 按目标语种声明 `Inter` / `Noto Sans SC` / `Noto Sans KR` / `Noto Sans Thai` / `Noto Naskh Arabic` 字体名，不嵌入系统商业字体；接收电脑安装对应开源字体即可保持一致版式。

旧 SourceHanSansCN-Medium.ttf 的来源尚未重新确认，S12 渲染不再使用或随生产镜像分发，Git 历史保留。字体核验属于工程检查，人工法务确认仍待完成。

Windows 开发验证需要官方 MSYS2 UCRT64 的 Pango 动态库，设置 WEASYPRINT_DLL_DIRECTORIES；Linux 容器安装 Debian 的 libpango-1.0-0、libpangoft2-1.0-0、libharfbuzz-subset0，以及供 LibreOffice 无头转换官方申报表格使用的 fonts-liberation（OFL-1.1）与 fonts-dejavu-core（Bitstream Vera / Arev 宽松许可，覆盖 U+2610/U+2611 复选框字形），并在镜像构建阶段由上述已校验 `NotoSansSC[wght].ttf` 生成静态 Regular(400)/Bold(700) 实例（保留 `Noto Sans SC` 字族名与 OFL.txt 原文，不改动 `backend/app/analysis/fonts/` 原文件字节）。原生库保持未修改、动态加载，许可证原件留在系统包 copyright 目录。
