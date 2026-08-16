from __future__ import annotations

from hypothesis import strategies as st

json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10_000, max_value=10_000),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=80),
)

json_values = st.recursive(
    json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(max_size=20), children, max_size=5),
    ),
    max_leaves=20,
)

json_directive_types = json_values.filter(lambda value: value is not None and value != "message")

json_objects = st.dictionaries(
    st.text(max_size=20),
    json_values,
    max_size=8,
)

json_arrays = st.lists(json_values, max_size=8)
