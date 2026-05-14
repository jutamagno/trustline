from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_elastic_client
from trustline.search.elastic import ElasticClient

router = APIRouter()


class NLSearchRequest(BaseModel):
    query: str
    size: int = 20


@router.post("")
def natural_language_search(
    req: NLSearchRequest,
    es: ElasticClient = Depends(get_elastic_client),
) -> dict:
    from trustline.llm.client import get_bedrock_client
    llm = get_bedrock_client()
    es_query = es.natural_language_to_query(req.query, llm_client=llm)
    results = es.search_events(es_query, size=req.size)
    return {"query": req.query, "results": results, "count": len(results)}
