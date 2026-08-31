# False positive TC002 on SQLAlchemy 2.0 `Mapped` annotations

## Description
We are updating our codebase to use the new SQLAlchemy 2.0 declarative syntax. When defining columns using `Mapped[Type]`, the linter raises a **TC002** error.

However, if we follow the linter's suggestion and move the `Mapped` import into a `if TYPE_CHECKING:` block, the application crashes when attempting to run.

## Reproduction Script
```python
from sqlalchemy.orm import DeclarativeBase, Mapped

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "user"

    # The linter flags the import used here
    id: Mapped[int]
    name: Mapped[str]
```

## Actual Behavior
The linter produces an error:
`TC002 Move third-party import 'sqlalchemy.orm.Mapped' into a type-checking block`

## Expected Behavior
The linter should not flag the `Mapped` import as exclusively for type checking in this context, as the code must be able to execute successfully.

The repository is at `/workspace/flake8-type-checking`, checked out at commit `9855cdae692608d50a25e51f7e57579438769f28`.