# `src/kanibako/secret_delivery.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/secret_delivery.py.md`.


## Variables

```
SECRET_DATA_FD: Final[int] = 3
SECRET_ACK_FD: Final[int] = 4
SECRET_READY_ACK: Final[bytes] = b'R'
_FRAME_MAGIC: Final[bytes] = b'KANISEC\x01'
_COUNT = struct.Struct('!I')
_LENGTH = struct.Struct('!Q')
_READ_CHUNK: Final[int] = 64 * 1024
```

## Functions
```
def iter_payload(payload: SecretPayload) -> Iterator[bytes]
def encode_payload(payload: SecretPayload) -> bytes
def read_payload(stream: BinaryIO) -> SecretPayload
def decode_payload(frame: bytes) -> SecretPayload
def receive_environment(*, data_fd: int=SECRET_DATA_FD, ack_fd: int=SECRET_ACK_FD) -> Mapping[str, str]
def main(argv: list[str] | None=None) -> int
def _read_piece(stream: BinaryIO, size: int) -> bytes
def _read_exact(stream: BinaryIO, size: int) -> bytes
```

## Classes

```
class SecretDeliveryError(ValueError):

@dataclass(frozen=True)
class SecretValue:
    name: str
    value: bytes = field(repr=False)

    def __post_init__(self) -> None

@dataclass(frozen=True)
class SecretPayload:
    values: tuple[SecretValue, ...] = field(default=(), repr=False)

    @property
    def names(self) -> tuple[str, ...]

    def __post_init__(self) -> None
```
