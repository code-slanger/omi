from .index import _get_client, _get_ef, _collection_name, collection_count


def retrieve(
    user_id: str,
    query: str,
    n_results: int = 5,
    media_type: str | None = None,
    source_type: str | None = None,
) -> list[dict]:
    """
    Retrieve the top-n most relevant documents from a user's corpus.
    Returns list of {"text": str, "metadata": dict, "distance": float}
    """
    count = collection_count(user_id)
    if count == 0:
        return []

    try:
        col = _get_client().get_collection(
            name=_collection_name(user_id),
            embedding_function=_get_ef(),
        )
    except Exception:
        return []

    filters = []
    if media_type:
        filters.append({"media_type": {"$eq": media_type}})
    if source_type:
        filters.append({"source_type": {"$eq": source_type}})

    where = {"$and": filters} if len(filters) > 1 else filters[0] if filters else None
    k = min(n_results, count)

    try:
        results = col.query(
            query_texts=[query],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        return []

    output = []
    docs = results.get("documents") or [[]]
    metas = results.get("metadatas") or [[]]
    dists = results.get("distances") or [[]]

    for text, meta, dist in zip(docs[0], metas[0], dists[0]):
        output.append({"text": text, "metadata": meta or {}, "distance": dist})

    return output
