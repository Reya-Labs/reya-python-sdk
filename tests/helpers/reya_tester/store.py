"""Generic state store for WebSocket and REST event tracking.

This module provides a unified, type-safe storage pattern for all execution
and event types (perp, spot, orders, positions, balances).
"""

from typing import Callable, Generic, Optional, TypeVar, Union, overload

from collections.abc import Iterator, KeysView

T = TypeVar("T")


class EventStore(Generic[T]):
    """Generic store for any event/execution type.

    Provides both list-based storage (for searching) and optional
    key-based access (for direct lookups by ID).

    Type Parameters:
        T: The type of items stored (e.g., PerpExecution, SpotExecution).

    Example:
        # Store with key-based access
        orders = EventStore[Order](key_fn=lambda o: o.order_id)
        orders.add(order)
        order = orders.get("order-123")

        # Store with list-only access
        executions = EventStore[PerpExecution]()
        executions.add(execution)
        match = executions.find(lambda e: e.symbol == "ETH")
    """

    def __init__(self, key_fn: Optional[Callable[[T], str]] = None) -> None:
        """Initialize the store.

        Args:
            key_fn: Optional function to extract a unique key from items.
                    If provided, enables get() for direct key-based lookups.
        """
        self._items: list[T] = []
        self._by_key: dict[str, T] = {}
        self._key_fn = key_fn

    def add(self, item: T) -> None:
        """Add an item to the store.

        Args:
            item: The item to add.
        """
        self._items.append(item)
        if self._key_fn is not None:
            key = self._key_fn(item)
            self._by_key[key] = item

    def find(self, predicate: Callable[[T], bool]) -> Optional[T]:
        """Find the first item matching a predicate.

        Args:
            predicate: Function that returns True for matching items.

        Returns:
            The first matching item, or None if not found.
        """
        return next((item for item in self._items if predicate(item)), None)

    def find_all(self, predicate: Callable[[T], bool]) -> list[T]:
        """Find all items matching a predicate.

        Args:
            predicate: Function that returns True for matching items.

        Returns:
            List of all matching items (may be empty).
        """
        return [item for item in self._items if predicate(item)]

    def get(self, key: str) -> Optional[T]:
        """Get an item by its key (requires key_fn in constructor).

        Args:
            key: The key to look up.

        Returns:
            The item with that key, or None if not found.

        Raises:
            RuntimeError: If store was created without a key_fn.
        """
        if self._key_fn is None:
            raise RuntimeError("Cannot use get() on a store without key_fn")
        return self._by_key.get(key)

    def contains_key(self, key: str) -> bool:
        """Check if a key exists in the store.

        Args:
            key: The key to check.

        Returns:
            True if the key exists, False otherwise.
        """
        if self._key_fn is None:
            raise RuntimeError("Cannot use contains_key() on a store without key_fn")
        return key in self._by_key

    def clear(self) -> None:
        """Remove all items from the store."""
        self._items.clear()
        self._by_key.clear()

    @property
    def last(self) -> Optional[T]:
        """Get the most recently added item.

        Returns:
            The last item added, or None if store is empty.
        """
        return self._items[-1] if self._items else None

    @property
    def all(self) -> list[T]:
        """Get a copy of all items in the store.

        Returns:
            List of all items (copy, not reference).
        """
        return self._items.copy()

    def __len__(self) -> int:
        """Return the number of items in the store."""
        return len(self._items)

    def __iter__(self) -> Iterator[T]:
        """Iterate over all items in the store."""
        return iter(self._items)

    def __bool__(self) -> bool:
        """Return True if store has items, False if empty."""
        return len(self._items) > 0

    def __contains__(self, key: str) -> bool:
        """Check if a key exists (dict-like 'in' operator support)."""
        if self._key_fn is None:
            raise RuntimeError("Cannot use 'in' operator on a store without key_fn")
        return key in self._by_key

    def keys(self) -> KeysView[str]:
        """Return keys view (dict-like compatibility)."""
        if self._key_fn is None:
            raise RuntimeError("Cannot use keys() on a store without key_fn")
        return self._by_key.keys()

    @overload
    def __getitem__(self, key: int) -> T: ...  # noqa: E704

    @overload
    def __getitem__(self, key: slice) -> list[T]: ...  # noqa: E704

    @overload
    def __getitem__(self, key: str) -> T: ...  # noqa: E704

    def __getitem__(self, key: Union[int, slice, str]) -> Union[T, list[T]]:
        """Get item by index, slice, or key.

        Supports:
            - Integer index: store[0], store[-1]
            - Slice: store[1:3], store[:-1]
            - String key: store["order-123"] (requires key_fn)
        """
        if isinstance(key, int):
            return self._items[key]
        elif isinstance(key, slice):
            return self._items[key]
        else:
            if self._key_fn is None:
                raise RuntimeError("Cannot use string key on a store without key_fn")
            return self._by_key[key]
