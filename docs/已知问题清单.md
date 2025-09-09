# 已知问题清单

## 重要问题

### revisit xml工具解析系统

`xml_parser.py`解析出来的所有参数格式应该都是str，但是实际调用工具的时候参数可能是`int`、`List[str]`，是不是工具自己在`excute`之前需要正确进行类型转换？不过实际好像能正确调用：

```xml
> user:
  Extract detailed information about the Quantinuum System Model H2 quantum processor breakthrough from https://www.163.com/dy/article/JHFE1EI4051494RM.html

> assistant:
  <tool_call>
    <name>web_fetch</name>
    <params>
      <urls>["https://www.163.com/dy/article/JHFE1EI4051494RM.html"]</urls>
      <max_content_length>5000</max_content_length>
      <max_concurrent>1</max_concurrent>
    </params>
  </tool_call>

> user:
  <tool_result>
    <name>web_fetch</name>
    <success>true</success>
    <data>
      <fetch_results>
        <fetch_result>
          <url>https://www.163.com/dy/article/JHFE1EI4051494RM.html</url>
          <title>Quantinuum荣获2024 Fast Company大奖|量子计算|quantinuum_网易订阅</title>
          <content>...</content>
          <word_count>489</word_count>
          <fetched_at>2025-09-04T14:47:44.872237</fetched_at>
        </fetch_result>
      </fetch_results>
    </data>
  </tool_result>
```

how?

### artifact rewrite准确度

agent无法正确调用artifact的update功能失败调用日志：
```xml
> assistant:
  <tool_call>
    <name>read_artifact</name>
    <params>
      <id>task_plan</id>
    </params>
  </tool_call>

> user:
  <tool_result>
    <name>read_artifact</name>
    <success>true</success>
    <data>
      <id>task_plan</id>
      <content_type>markdown</content_type>
      <title>Task: Research Quantum Computing Breakthroughs in 2024</title>
      <content># Task: Research Quantum Computing Breakthroughs in 2024
      
      ## Objective
      Identify and summarize major breakthroughs and advancements in quantum computing achieved during 2024.
      
      ## Tasks
      1. [✗] Conduct a web search to identify key breakthroughs in quantum computing in 2024.
         - Status: pending
         - Assigned: search_agent
         - Notes: 
      
      2. [✗] Extract detailed information from relevant sources identified in the search.
         - Status: pending
         - Assigned: crawl_agent
         - Notes: Requires specific URLs from Task 1.
      
      3. [✗] Synthesize findings and summarize the most impactful breakthroughs.
         - Status: pending
         - Assigned: lead_agent
         - Notes: 
      
      ## Progress Summary
      - Overall: 0%
      - Last Updated: 2024-06-13</content>
      <version>1</version>
      <updated_at>2025-09-04T14:46:53.361252</updated_at>
    </data>
  </tool_result>

> assistant:
  <tool_call>
    <name>update_artifact</name>
    <params>
      <id>task_plan</id>
      <old_str>1. [✗] Conduct a web search to identify key breakthroughs in quantum computing in 2024.\n   - Status: pending\n   - Assigned: search_agent\n   - Notes: </old_str>
      <new_str>1. [✓] Conduct a web search to identify key breakthroughs in quantum computing in 2024.\n   - Status: completed\n   - Assigned: search_agent\n   - Notes: Identified key breakthroughs from Quantinuum, IBM, and other research institutions.</new_str>
    </params>
  </tool_call>

> user:
  <tool_result>
    <name>update_artifact</name>
    <success>false</success>
    <error>Text '1. [✗] Conduct a web search to identify key breakt...' not found in artifact</error>
  </tool_result>
```

是否是read之后tool result展示格式问题？还是说这个工具的正则解析有问题？还是说`xml_parser.py`解析的调用参数不对？



### 优化agent提示词格式：xml

lead agent加入task plan artifact之后提示词太乱了，需要用xml标签分类规整一下，例如task_plan应该用`<task_plan>`这样的xml标签框起来（其他agent最好也加一下）

### crawl agent无法正确调用web_fetch

虽然提示词里给了例子，然而crawl agent没办法正确生成list格式的url list:
```xml
> system:
  You are crawl_agent, a specialized agent for web content extraction and cleaning.
  
  ## Your Mission
  
  Extract and clean valuable information from web pages.
  
  ## Team Context
  
  You are part of a multi-agent research team. The Lead Agent coordinates overall strategy while you focus on deep content extraction.
  
  ## Team Task Plan
  
  The following is our team's current task plan. Use this to understand what information is most valuable to extract:
  
  # Task: Research and Analyze AI Impact on Healthcare in 2024
  
  ## Objective
  Research and analyze the impact of AI on healthcare in 2024, including recent developments, key players, and future trends.
  
  ## Tasks
  1. [✓] Conduct initial research on AI applications in healthcare for 2024
     - Status: completed
     - Assigned: search_agent
     - Notes: Found key developments including AI in drug discovery (Huawei Cloud's Pangu Model, Insilico Intelligence's ChatPandaGPT), medical imaging (Deepwise Medical's SAMI, United Imaging's uAI), clinical decision support (MedGPT, DingDang HealthGPT), and personalized treatment. AI adoption in China exceeds global average with over one-third of healthcare professionals using AI tools. FDA approved 692 AI medical devices as of July 2023, a 33% increase from 2022.
  
  2. [✓] Identify key players and organizations in AI healthcare space
     - Status: completed
     - Assigned: search_agent
     - Notes: Identified key players in various AI healthcare domains: AI drug development (e.g., Huawei Cloud's Pangu Model, Insilico Intelligence's ChatPandaGPT), medical imaging (e.g., Deepwise Medical's SAMI, United Imaging's uAI), clinical decision support systems (e.g., MedGPT, DingDang HealthGPT). Chinese companies like Alibaba Health, JD Health, and Ping An Health are leading in AI healthcare adoption. Additionally, startups like Abridge, Hippocratic AI, and Regard are notable in the global market.
  
  3. [✗] Extract detailed information from relevant sources about developments and trends
     - Status: pending
     - Assigned: crawl_agent
     - Notes:
  
  4. [✗] Synthesize information about AI's impact on healthcare
     - Status: pending
     - Assigned: lead_agent
     - Notes:
  
  5. [✗] Create comprehensive analysis report
     - Status: pending
     - Assigned: lead_agent
     - Notes:
  
  ## Progress Summary
  - Overall: 40%
  - Last Updated: 2025-09-04 3:00 PM
  
  **Your Role**: Extract detailed content that supports this plan, focusing on relevant sections.
  
  ## Core Capabilities
  
  1. **Content Extraction**: Fetch and identify main content
  2. **Content Cleaning**: Remove ads, navigation, and irrelevant sections
  3. **Quality Assessment**: Detect anti-crawling, paywalls, or invalid content
  4. **Concise Output**: Return only valuable information
  
  ## Extraction Process
  
  1. Fetch content from URLs
  2. Assess content quality
  3. Clean and extract key information
  4. Format results
  
  ## Output Format
  
  Return extracted content in this simple XML structure:
  
  <extracted_pages>
    <page>
      <url>https://...</url>
      <title>Page Title</title>
      <content>Cleaned and extracted main content</content>
    </page>
    <!-- More pages if needed -->
  </extracted_pages>

  ## Important Notes

  - If content seems invalid (anti-crawling, paywall, error page), mention it in content field
  - Focus on main content, skip navigation/ads/footers
  - Keep content concise but informative
  - Don't force extraction from clearly invalid pages

  ## Tool Usage

  You have access to the web_fetch tool with these parameters:
  - urls: Single URL or list of URLs (required)
  - max_content_length: Maximum content per page (default 5000)
  - max_concurrent: Concurrent fetches (default 3, max 5)

  You have access to the following tools. To use a tool, format your request in XML:

  <tool_call>
    <name>tool_name</name>
    <params>
      <param_name>param_value</param_name>
    </params>
  </tool_call>

  Available tools:

  ### web_fetch
  Description: Fetch and extract content from web pages
  Parameters:
    - urls: array[string] (required) - URL or list of URLs to fetch
    - max_content_length: integer (optional) - Maximum content length per page in characters (default: 5000)
      Default: 5000
    - max_concurrent: integer (optional) - Maximum concurrent browser instances (default: 3, max: 5) - Each browser uses ~100-300MB memory
      Default: 3
  Example:
  <tool_call>
    <name>web_fetch</name>
    <params>
      <urls>["item1", "item2"]</urls>
      <max_content_length>5000</max_content_length>
      <max_concurrent>3</max_concurrent>
    </params>
  </tool_call>


  Important guidelines:
  1. Always use the exact tool name as specified
  2. Include all required parameters
  3. Use proper XML formatting with closed tags
  4. You can make multiple tool calls in sequence
  5. Wait for tool results before proceeding with analysis


> user:
  Extract detailed information about AI's impact on healthcare in 2024 from the following URLs:
  https://www.chinabaogao.com/baogao/202412/737463.html
  https://t.qianzhan.com/caijing/detail/241012-05e76f03.html
  https://www.sohu.com/a/835515909_121124565
  https://www.toutiao.com/article/7418519449646432809/
  https://licai.cofool.com/ask/vipqa_3557588_32179930.html
  https://www.laohu8.com/m/post/385685258687016

> assistant:
  <tool_call>
    <name>web_fetch</name>
    <params>
      <urls>
        "https://www.chinabaogao.com/baogao/202412/737463.html"
        "https://t.qianzhan.com/caijing/detail/241012-05e76f03.html"
        "https://www.sohu.com/a/835515909_121124565"
        "https://www.toutiao.com/article/7418519449646432809/"
        "https://licai.cofool.com/ask/vipqa_3557588_32179930.html"
        "https://www.laohu8.com/m/post/385685258687016"
      </urls>
      <max_content_length>5000</max_content_length>
      <max_concurrent>3</max_concurrent>
    </params>
  </tool_call>

> user:
  <tool_result>
    <name>web_fetch</name>
    <success>true</success>
    <data>
      <fetch_results>
        <fetch_error>
          <url>&quot;https://www.chinabaogao.com/baogao/202412/737463.html&quot;
            &quot;https://t.qianzhan.com/caijing/detail/241012-05e76f03.html&quot;
            &quot;https://www.sohu.com/a/835515909_121124565&quot;
            &quot;https://www.toutiao.com/article/7418519449646432809/&quot;
            &quot;https://licai.cofool.com/ask/vipqa_3557588_32179930.html&quot;
            &quot;https://www.laohu8.com/m/post/385685258687016&quot;</url>
          <error>Unexpected error in _crawl_web at line 500 in crawl (..\..\..\software\miniconda3\envs\artifact-flow\lib\site-packages\crawl4ai\async_crawler_strategy.py):
      Error: URL must start with &apos;http://&apos;, &apos;https://&apos;, &apos;file://&apos;, or &apos;raw:&apos;

      Code context:
       495                   status_code=status_code,
       496                   screenshot=screenshot_data,
       497                   get_delayed_content=None,
       498               )
       499           else:
       500 →             raise ValueError(
       501                   &quot;URL must start with &apos;http://&apos;, &apos;https://&apos;, &apos;file://&apos;, or &apos;raw:&apos;&quot;
       502               )
       503   
       504       async def _crawl_web(
       505           self, url: str, config: CrawlerRunConfig</error>
        </fetch_error>
      </fetch_results>
    </data>
  </tool_result>
```

是不是因为xml格式不太常见下面list的写法：
```xml
<tool_call>
    <name>web_fetch</name>
    <params>
      <urls>["item1", "item2"]</urls>
      <max_content_length>5000</max_content_length>
      <max_concurrent>3</max_concurrent>
    </params>
</tool_call>
```





## 次要问题

### agent提示词增加系统时间

agent应该感知系统时间：`2025/09/04 16:04:32 Wed.`

### artifact通用化描述

lead agent的提示词中不应该指定result artifact为：`### Result Artifact (ID: "result")`，感觉应该更通用一点，例如用户想要一个report，这个id就应该为"xxx report"，如果用户想要的是一个脚本，那就应该是例如"xxx.py"。

同样的我希望task_plan的作用也能更versatile一点，agent应该能在上面记笔记，例如将需要爬取的网页记录在上面

### agent node对话历史处理

每个agent从自己的工具循环return之后，之前这个循环的历史记录就没了，感觉需要把这部分内容也return出来。然后在任务编排的时候，例如在`multi_agent_test.py`或者之后的langgraph编排中可以选择性的把这部分历史记录加回去。

### task_plan artifact没有及时加载

BaseAgent只会在tool调用循环前构建系统提示词并调用_format_messages_for_debug，这样如果在工具循环中的操作不会及时更新到系统提示词中。

### search agent结果返回方式

感觉让search agent再复述一遍答案没比较，要不还是直接return工具结果。search agent的职责就是搜索并判断现在搜索内容符不符合lead agent要求，符合就给个准过

### 精简提示词

简化合并提示词内容
