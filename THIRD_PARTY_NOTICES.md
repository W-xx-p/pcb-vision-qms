# 第三方组件与数据声明

本仓库自身代码按 [MIT License](LICENSE) 发布。依赖、数据集及其商标仍分别受各自条款约束，本仓库的 MIT License 不会对这些第三方材料重新授权。

## Anomalib

项目通过可选依赖使用 [Anomalib](https://github.com/open-edge-platform/anomalib) 2.2.0 提供 PatchCore、PaDiM 和 EfficientAD 能力。Anomalib 不以源码形式复制到本仓库；安装和使用时请遵守其上游许可证及依赖组件条款。

## Amazon VisA

本仓库不分发 VisA 原始图片或压缩包。`scripts/prepare_visa.py` 仅从公开来源下载数据，并生成供复现使用的文件清单；`docs/benchmarks/visa/` 只包含衍生指标、摘要和数据清单。下载、使用或再分发数据前，请自行确认并遵守 VisA 作者提供的数据集条款。

## 其他依赖

Python 包、容器基础镜像、数据库、中间件和前端资源均保留其各自的许可证、版权和商标。部署方应根据实际启用的组件完成软件物料清单和许可证合规审查。
