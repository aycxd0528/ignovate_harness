"""Lossless, bounded compression of the existing checkpoint serializer bytes."""
import zlib

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


MAX_CHECKPOINT_BYTES = 64 * 1024 * 1024
_MIN_COMPRESS_BYTES = 16 * 1024
_FAMILY = 'nailong.zlib.'
_PREFIX = _FAMILY + 'v1:'
_KINDS = {'msgpack', 'bytes', 'bytearray'}


class CheckpointSerializer(JsonPlusSerializer):
    def dumps_typed(self, obj):
        kind, body = super().dumps_typed(obj)
        if kind in _KINDS and _MIN_COMPRESS_BYTES <= len(body) <= MAX_CHECKPOINT_BYTES:
            compressed = zlib.compress(body, 3)
            if len(compressed) * 8 <= len(body) * 7:
                return _PREFIX + kind + ':' + str(len(body)), compressed
        # Small, incompressible and oversized native records keep their format.
        return kind, body

    def loads_typed(self, data):
        kind, body = data
        if isinstance(kind, str) and kind.startswith(_FAMILY):
            if not kind.startswith(_PREFIX) or len(kind) > 64:
                raise ValueError('不支持的 checkpoint 压缩格式。')
            try:
                inner, length = kind[len(_PREFIX):].split(':')
                if len(length) > 8:
                    raise ValueError('checkpoint 长度无效。')
                size = int(length)
                if (inner not in _KINDS or str(size) != length
                        or not 0 <= size <= MAX_CHECKPOINT_BYTES
                        or len(body) > MAX_CHECKPOINT_BYTES):
                    raise ValueError('checkpoint 压缩头无效。')
                decoder = zlib.decompressobj()
                raw = decoder.decompress(body, size + 1)
                if (len(raw) != size or not decoder.eof
                        or decoder.unused_data or decoder.unconsumed_tail):
                    raise ValueError('checkpoint 压缩正文不完整。')
            except (TypeError, ValueError, zlib.error) as error:
                raise ValueError('checkpoint 压缩记录损坏或超限。') from error
            kind, body = inner, raw
        try:
            return super().loads_typed((kind, body))
        except NotImplementedError as error:
            raise ValueError('无法读取 checkpoint 编码。') from error
