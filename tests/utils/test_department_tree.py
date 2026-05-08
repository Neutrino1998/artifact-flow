"""Tests for utils.department_tree.expand_subtree."""

from dataclasses import dataclass

from utils.department_tree import expand_subtree


@dataclass
class _FakeDept:
    """Lightweight stand-in for db.models.Department — only the attrs the
    helper reads (id, parent_id) — so we can test expand_subtree without
    DB setup."""
    id: str
    parent_id: str | None


def _build(depts):
    """Convert list of (id, parent_id) tuples to FakeDept list."""
    return [_FakeDept(i, p) for i, p in depts]


class TestExpandSubtree:
    def test_empty_seeds(self):
        depts = _build([("a", None), ("b", "a")])
        assert expand_subtree(depts, set()) == set()

    def test_seed_not_in_depts(self):
        depts = _build([("a", None)])
        # Seed id with no matching dept — still returned (caller-provided)
        # but no descendants found
        assert expand_subtree(depts, {"missing"}) == {"missing"}

    def test_single_root_no_children(self):
        depts = _build([("a", None)])
        assert expand_subtree(depts, {"a"}) == {"a"}

    def test_single_root_with_descendants(self):
        # a → b → c
        #   → d
        depts = _build([("a", None), ("b", "a"), ("c", "b"), ("d", "a")])
        assert expand_subtree(depts, {"a"}) == {"a", "b", "c", "d"}

    def test_seed_in_middle(self):
        # a → b → c → d
        depts = _build([("a", None), ("b", "a"), ("c", "b"), ("d", "c")])
        # Starting from b, only b/c/d
        assert expand_subtree(depts, {"b"}) == {"b", "c", "d"}

    def test_multiple_seeds(self):
        # a → b
        # x → y
        depts = _build([("a", None), ("b", "a"), ("x", None), ("y", "x")])
        assert expand_subtree(depts, {"a", "x"}) == {"a", "b", "x", "y"}

    def test_disjoint_branches(self):
        depts = _build([
            ("a", None), ("b", "a"),
            ("x", None), ("y", "x"),
            ("z", None),  # untouched root
        ])
        result = expand_subtree(depts, {"a"})
        assert result == {"a", "b"}
        assert "x" not in result
        assert "z" not in result

    def test_depth_3(self):
        # Linear chain depth 3
        depts = _build([
            ("L0", None), ("L1", "L0"), ("L2", "L1"), ("L3", "L2"),
        ])
        assert expand_subtree(depts, {"L0"}) == {"L0", "L1", "L2", "L3"}
        assert expand_subtree(depts, {"L2"}) == {"L2", "L3"}

    def test_overlapping_seeds(self):
        # If seed includes an ancestor + descendant, result is still subtree of ancestor
        depts = _build([("a", None), ("b", "a"), ("c", "b")])
        assert expand_subtree(depts, {"a", "b"}) == {"a", "b", "c"}
