# M0 合成服务工单样本

**全部设备台账、站点、工单和 SLA 策略均为虚构数据。** 不是 LulzBot 或任何企业的实际服务记录。

在仓库根目录运行：

```bash
python -m service_operations.generate_m0
```

`sample_manifest.json` 保存配置哈希、各文件行数和 SHA-256。`reference_cases.json` 保存五个指标案例的参考值与工单 ID。`invalid/` 是故意非法的拒收样本，不属于有效快照。

业务口径、合成规则与官方手册来源分别见 [M0 说明](../../docs/M0_FOUNDATION.md) 和 [手册来源清单](../../docs/manual_sources.json)。
