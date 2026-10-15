"""
AI service for natural language to SQL conversion.
Handles OpenAI API interactions and prompt engineering.
"""
from typing import List, Dict
from openai import OpenAI
from backend.models.models import DatabaseSchema, QueryRequest
from backend.config.config import Config
import logging

logger = logging.getLogger(__name__)


class AIService:
    """Service for AI-powered natural language to SQL conversion."""
    
    def __init__(self):
        """Initialize AI service with OpenAI client."""
        try:
            # Check if API key exists
            if not Config.get_provider():
                raise ValueError("OPENAI_API_KEY or OPENROUTER_API_KEY not found in environment variables")
            
            self.client = OpenAI(**Config.get_openai_config())
            self.model = Config.get_model()
            self.ai_available = True
            logger.info(f"AI service initialized successfully with model: {self.model}")
        except Exception as e:
            logger.error(f"AI service initialization failed: {e}")
            self.client = None
            self.model = None
            self.ai_available = False
    
    def convert_natural_to_sql(
        self, 
        request: QueryRequest, 
        schema: DatabaseSchema
    ) -> str:
        """
        Convert natural language question to SQL query.
        
        Args:
            request: Query request with question and tables
            schema: Database schema information
            
        Returns:
            Generated SQL query string
            
        Raises:
            ValueError: If request is invalid
            Exception: If AI service fails
        """
        try:
            # Validate request
            if not request.question.strip():
                raise ValueError("Question cannot be empty")
            
            if not request.tables:
                raise ValueError("At least one table must be specified")
            
            # Get relevant schema information
            relevant_schema = self._get_relevant_schema(request.tables, schema)
            
            # Generate prompt
            prompt = self._generate_prompt(request.question, relevant_schema)
            
            # Call OpenAI API
            response = self._call_openai_api(prompt)
            
            # Clean and validate SQL
            sql_query = self._clean_sql_response(response)
            
            logger.info(f"Generated SQL query: {sql_query}")
            return sql_query
            
        except Exception as e:
            logger.error(f"Error converting natural language to SQL: {e}")
            raise
    
    def _get_relevant_schema(
        self, 
        table_names: List[str], 
        schema: DatabaseSchema
    ) -> Dict[str, List[str]]:
        """Get schema information for relevant tables only."""
        relevant_schema = {}
        
        for table_name in table_names:
            if table_name in schema.tables:
                relevant_schema[table_name] = schema.get_table_columns(table_name)
            else:
                logger.warning(f"Table '{table_name}' not found in schema")
        
        return relevant_schema
    
    def _generate_prompt(self, question: str, schema: Dict[str, List[str]]) -> str:
        """Generate prompt for OpenAI API."""
        schema_str = self._format_schema_for_prompt(schema)
        
        prompt = f"""
You are an expert SQL developer specializing in SQL Server. 
Your task is to convert natural language questions into accurate SQL Server queries.

Database Schema:
{schema_str}

Instructions:
1. Use ONLY the tables and columns provided in the schema above
2. Write valid SQL Server syntax
3. Use proper JOINs when multiple tables are involved
4. Include appropriate WHERE clauses for filtering
5. Use meaningful column aliases when needed
6. Return ONLY the SQL query, no explanations or markdown formatting

Question: {question}

SQL Query:
"""
        return prompt.strip()
    
    def _format_schema_for_prompt(self, schema: Dict[str, List[str]]) -> str:
        """Format schema information for the prompt."""
        schema_lines = []
        for table_name, columns in schema.items():
            columns_str = ", ".join(columns)
            schema_lines.append(f"{table_name}: {columns_str}")
        
        return "\n".join(schema_lines)
    
    def _call_openai_api(self, prompt: str) -> str:
        """Call OpenAI API with the generated prompt."""
        try:
            # For OpenAI v2.3.0+ (Python 3.10+ compatible)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.1
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"OpenAI API call failed: {e}")
            raise Exception(f"AI service error: {str(e)}")
    
    def _clean_sql_response(self, response: str) -> str:
        """Clean and validate SQL response from AI."""
        # Remove markdown code blocks if present
        sql_query = response.replace("```sql", "").replace("```", "").strip()
        
        # Basic validation
        if not sql_query:
            raise ValueError("Empty SQL query generated")
        
        # Ensure it starts with a valid SQL keyword
        sql_upper = sql_query.upper().strip()
        valid_keywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'WITH']
        
        if not any(sql_upper.startswith(keyword) for keyword in valid_keywords):
            raise ValueError(f"Invalid SQL query: {sql_query}")
        
        return sql_query

    def summarize_sql(self, question: str, sql_query: str) -> Dict[str, str]:
        """Generate a brief natural-language summary and conversational analysis for a SQL query.

        Returns a dict with keys: summary (str) and analysis (str).
        """
        if not self.ai_available:
            logger.warning("AI service not available, returning fallback SQL summary")
            return {
                "summary": f"Bu sorgu '{question}' sorusuna yanıt veriyor. SQL sorgusu başarıyla oluşturuldu.",
                "analysis": f"Sorgu sonuçlarına göre verilerinizde önemli bilgiler bulunuyor. Analiz için sonuçları inceleyebilirsiniz."
            }
        
        try:
            prompt = f"""
Sen bir iş analisti ve rapor uzmanısın. Kullanıcının sorusu ve SQL sorgusu verilmiş. 
Türkçe olarak şunları üret:

1) Kısa açıklama: Sorgunun ne yaptığını 2-3 cümleyle açıkla
2) Doğal analiz: Kullanıcıyla konuşur gibi, sonuçlarda ne göreceğini detaylı anlat. 
   Örnek: "Bu rapora göre 2023 yılında toplam satışlarınız X miktarında gerçekleşmiş. 
   En yüksek performansı Y ayında göstermişsiniz, Z kategorisinde ise dikkat çekici bir artış var. 
   Müşteri segmentlerinde A grubu %B payla öne çıkıyor ve C bölgesinde potansiyel görüyorum."
   
   Gerçek veri olmadığı için genel eğilimleri, karşılaştırmaları, önemli noktaları vurgula.
   Sanki kullanıcıya rapor sunuyormuş gibi yaz. 4-5 cümle, samimi ve profesyonel ton.

Kullanıcı sorusu:
{question}

SQL sorgusu:
{sql_query}

Format:
- Summary: <2-3 cümle>
- Analysis: <4-5 cümle, doğal konuşma tarzı>
"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.3
            )
            content = response.choices[0].message.content.strip()

            # Simple parsing: split summary and analysis
            lines = [l.strip() for l in content.splitlines() if l.strip()]
            summary_lines = []
            analysis_lines: List[str] = []
            mode = "summary"
            for line in lines:
                low = line.lower()
                if low.startswith("- summary") or low.startswith("summary:"):
                    mode = "summary"
                    continue
                if low.startswith("- analysis") or low.startswith("analysis:") or low.startswith("- analiz") or low.startswith("analiz:"):
                    mode = "analysis"
                    continue
                if mode == "summary":
                    summary_lines.append(line.lstrip("- "))
                elif mode == "analysis":
                    analysis_lines.append(line.lstrip("- "))

            summary = " ".join(summary_lines)
            analysis = " ".join(analysis_lines)
            return {"summary": summary, "analysis": analysis}
        except Exception as e:
            logger.error(f"OpenAI summary generation failed: {e}")
            # Fallback: very basic template
            return {
                "summary": "Sorgu, girilen soruya göre ilgili tabloları kullanarak verileri getirir.",
                "analysis": "Bu rapora göre verilerinizde genel eğilimler ve önemli noktalar görülüyor. Tarih bazında artış ve azalış dönemleri, en yüksek ve en düşük performans gösteren kategoriler ile dikkat çekici segmentler bulunuyor. Detaylı analiz için sonuçları inceleyebilirsiniz."
            }

    def summarize_results(self, question: str, sql_query: str, results: List[Dict]) -> Dict[str, str]:
        """Generate a brief Turkish business summary and conversational analysis over actual query results.

        Returns dict with keys: summary (str) and analysis (str).
        """
        if not self.ai_available:
            logger.warning("AI service not available, returning fallback summary")
            return {
                "summary": f"Bu sorgu '{question}' sorusuna yanıt veriyor. {len(results)} kayıt bulundu.",
                "analysis": f"Sorgu sonuçlarına göre {len(results)} kayıt analiz edildi. Verilerinizde önemli trendler ve karşılaştırmalar bulunuyor."
            }
        
        # Test AI connection
        try:
            test_response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Test"}],
                max_tokens=10
            )
            logger.info("AI connection test successful")
        except Exception as e:
            logger.error(f"AI connection test failed: {e}")
            return {
                "summary": f"Bu sorgu '{question}' sorusuna yanıt veriyor. {len(results)} kayıt bulundu.",
                "analysis": f"Sorgu sonuçlarına göre {len(results)} kayıt analiz edildi. Verilerinizde önemli trendler ve karşılaştırmalar bulunuyor."
            }
        
        try:
            # Limit payload size for the LLM
            sample = results[:50]
            sample_preview = sample[:10]
            
            # Format results as structured text for better AI understanding
            import json
            
            # Create a more readable format for AI
            results_text = "TABLO SONUÇLARI:\n"
            if sample_preview:
                headers = list(sample_preview[0].keys())
                results_text += "Sütunlar: " + ", ".join(headers) + "\n\n"
                
                for i, row in enumerate(sample_preview, 1):
                    results_text += f"Satır {i}:\n"
                    for key, value in row.items():
                        results_text += f"  {key}: {value}\n"
                    results_text += "\n"
            else:
                results_text += "Sonuç bulunamadı.\n"
            
            prompt = f"""
Sen bir iş analisti ve rapor uzmanısın. Kullanıcının sorusu, SQL sorgusu ve gerçek sonuçlar verilmiş.
Türkçe olarak şunları üret:

1) Kısa özet: Sorgunun ne yaptığını 2-3 cümleyle açıkla
2) Doğal analiz: Gerçek verilere bakarak kullanıcıyla konuşur gibi detaylı rapor sun. 
   Örnek: "Bu rapora göre 2023 yılında toplam satışlarınız 150.000 TL olarak gerçekleşmiş. 
   En yüksek performansı Aralık ayında 25.000 TL ile göstermişsiniz, yaz aylarında ise 
   düşüş yaşanmış. Müşteri segmentlerinde kurumsal müşteriler %60 payla öne çıkıyor ve 
   İstanbul bölgesinde potansiyel görüyorum."
   
   Gerçek sayıları, tarihleri, kategorileri kullan. Trendleri, karşılaştırmaları, 
   önemli noktaları vurgula. Sanki kullanıcıya rapor sunuyormuş gibi yaz. 
   4-5 cümle, samimi ve profesyonel ton.

Kullanıcı sorusu:
{question}

SQL:
{sql_query}

{results_text}

Format:
- Özet: <2-3 cümle>
- Analiz: <4-5 cümle, doğal konuşma tarzı, gerçek verilerle>
"""
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.3
            )
            content = response.choices[0].message.content.strip()

            # Simple parse similar to summarize_sql
            lines = [l.strip() for l in content.splitlines() if l.strip()]
            summary_lines: List[str] = []
            analysis_lines: List[str] = []
            mode = "summary"
            for line in lines:
                low = line.lower()
                if low.startswith("- özet") or low.startswith("- ozet") or low.startswith("özet:") or low.startswith("ozet:") or low.startswith("- summary") or low.startswith("summary:"):
                    mode = "summary"
                    continue
                if low.startswith("- analiz") or low.startswith("analiz:") or low.startswith("- analysis") or low.startswith("analysis:"):
                    mode = "analysis"
                    continue
                if mode == "summary":
                    summary_lines.append(line.lstrip("- "))
                elif mode == "analysis":
                    analysis_lines.append(line.lstrip("- "))

            summary = " ".join(summary_lines)
            analysis = " ".join(analysis_lines)
            return {"summary": summary, "analysis": analysis}
        except Exception as e:
            logger.error(f"OpenAI results summary failed: {e}")
            return {
                "summary": "Sonuçlar genel olarak beklenen eğilimleri gösteriyor; metrikleri dönem bazında karşılaştırın.",
                "analysis": "Bu rapora göre verilerinizde önemli trendler ve karşılaştırmalar görülüyor. Dönemsel artış ve azalış dönemleri, en yüksek performans gösteren kategoriler ve dikkat çekici segmentler bulunuyor. Detaylı analiz için sonuçları inceleyebilirsiniz."
            }