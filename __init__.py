from .nodes import IndexTranslateText, IndexTranslateSRT

NODE_CLASS_MAPPINGS = {"IndexTranslateText": IndexTranslateText, "IndexTranslateSRT": IndexTranslateSRT}
NODE_DISPLAY_NAME_MAPPINGS = {"IndexTranslateText": "Index Translate 本地翻译",
                                    "IndexTranslateSRT": "Index Translate SRT 字幕翻译"}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
