from app.services.extractor import ExtractedOpportunity, extract_opportunity


class RulesProvider:
    name = "rules"

    def extract(self, text: str) -> ExtractedOpportunity:
        return extract_opportunity(text)
