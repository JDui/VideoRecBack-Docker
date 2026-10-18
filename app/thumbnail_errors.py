from __future__ import annotations


def describe_thumbnail_error(error: str | None) -> dict[str, str]:
    text = (error or "").lower()
    if "moov atom not found" in text:
        return {"reason": "无法读取视频容器信息", "suggestion": "先检查原视频是否可完整播放或复制完整，再重试封面。"}
    if any(value in text for value in ("invalid nal", "decoding", "decoder", "invalid data")):
        return {"reason": "视频解码失败", "suggestion": "请先检查原视频能否播放；也可重试封面，失败不代表原文件已永久损坏。"}
    if any(value in text for value in ("webp", "encoder", "encode")):
        return {"reason": "封面图像编码失败", "suggestion": "可重试生成封面；持续失败时检查源视频与服务日志。"}
    if any(value in text for value in ("unavailable", "no such file", "permission denied")):
        return {"reason": "暂时无法读取原视频", "suggestion": "请检查视频目录挂载、文件是否存在及读取权限，再重试。"}
    if "metadata" in text:
        return {"reason": "无法读取视频信息", "suggestion": "请检查原视频能否正常播放及读取权限，再重试封面。"}
    return {"reason": "封面尚未生成成功", "suggestion": "可先重试封面，或打开视频设置检查文件信息。"}
