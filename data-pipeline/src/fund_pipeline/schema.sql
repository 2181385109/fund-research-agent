-- fund_data 表结构（PLAN §4.2；细化见 ADR-029）。由 fund_pipeline.load 以 fund_loader 账号整库重建。
-- 约定：比率一律存小数（0.012 表示 1.20%），只在展示时加 %；金额单位为元，份额/股数单位为份/股；
-- 每张表都有 source（数据来源）与 as_of（数据截止日 DATA_AS_OF）。

CREATE TABLE funds (
  fund_code       VARCHAR(6)   NOT NULL COMMENT '基金主代码（通常是 A 类份额代码）',
  fund_name       VARCHAR(64)  NOT NULL COMMENT '基金简称（去掉份额字母）',
  full_name       VARCHAR(128) NOT NULL COMMENT '基金全称',
  fund_type       VARCHAR(32)  NOT NULL COMMENT '基金类型，如 混合型-偏股、股票型、指数型-股票',
  theme           VARCHAR(16)  NOT NULL COMMENT '主题：医药医疗 / 科技',
  style           VARCHAR(8)   NOT NULL COMMENT 'active 主动管理 / index 指数',
  company         VARCHAR(64)  NOT NULL COMMENT '基金管理人',
  custodian       VARCHAR(64)  NOT NULL COMMENT '基金托管人',
  established     DATE         NOT NULL COMMENT '基金合同生效日（成立日）',
  benchmark       VARCHAR(512) NOT NULL COMMENT '业绩比较基准',
  tracking_index  VARCHAR(128) NULL     COMMENT '跟踪指数（指数基金），主动基金为 NULL',
  source          VARCHAR(128) NOT NULL,
  as_of           DATE         NOT NULL,
  PRIMARY KEY (fund_code)
) COMMENT='基金基本信息';

CREATE TABLE share_classes (
  share_code   VARCHAR(6)  NOT NULL COMMENT '份额代码',
  fund_code    VARCHAR(6)  NOT NULL COMMENT '所属基金主代码',
  share_class  VARCHAR(4)  NOT NULL COMMENT '份额类别 A / C；单一份额为空串',
  share_name   VARCHAR(64) NOT NULL COMMENT '份额简称',
  source       VARCHAR(128) NOT NULL,
  as_of        DATE        NOT NULL,
  PRIMARY KEY (share_code),
  KEY idx_share_fund (fund_code)
) COMMENT='基金份额类别';

CREATE TABLE fees (
  share_code         VARCHAR(6)    NOT NULL,
  management_fee     DECIMAL(8,6)  NOT NULL COMMENT '管理费率（年，小数）',
  custody_fee        DECIMAL(8,6)  NOT NULL COMMENT '托管费率（年，小数）',
  sales_service_fee  DECIMAL(8,6)  NOT NULL COMMENT '销售服务费率（年，小数；A 类通常为 0）',
  source             VARCHAR(128)  NOT NULL,
  as_of              DATE          NOT NULL,
  PRIMARY KEY (share_code)
) COMMENT='运作费用';

CREATE TABLE purchase_fee_tiers (
  share_code   VARCHAR(6)     NOT NULL,
  tier_no      INT            NOT NULL COMMENT '档位序号，从 1 开始',
  min_amount   DECIMAL(20,2)  NOT NULL COMMENT '申购金额下限（元，含）',
  max_amount   DECIMAL(20,2)  NULL     COMMENT '申购金额上限（元，不含）；NULL 表示无上限',
  rate         DECIMAL(8,6)   NULL     COMMENT '申购费率（小数）；按笔收取固定费用时为 NULL',
  fixed_fee    DECIMAL(12,2)  NULL     COMMENT '每笔固定费用（元）；按比例收费时为 NULL',
  tier_text    VARCHAR(128)   NOT NULL COMMENT '来源中的档位原文，如 100万元≤M＜500万元',
  source       VARCHAR(128)   NOT NULL COMMENT '来源，如 招募说明书 + doc_id',
  source_page  INT            NULL     COMMENT '来源 PDF 页码（从 1 开始）',
  as_of        DATE           NOT NULL,
  PRIMARY KEY (share_code, tier_no)
) COMMENT='申购费率分档（原费率，不含销售机构折扣；非养老金等特定投资群体）';

CREATE TABLE redemption_fee_tiers (
  share_code  VARCHAR(6)    NOT NULL,
  tier_no     INT           NOT NULL,
  min_days    INT           NOT NULL COMMENT '持有天数下限（含）',
  max_days    INT           NULL     COMMENT '持有天数上限（不含）；NULL 表示无上限',
  rate        DECIMAL(8,6)  NOT NULL COMMENT '赎回费率（小数）',
  tier_text   VARCHAR(128)  NOT NULL COMMENT '来源中的档位原文，如 大于等于7天，小于30天',
  source      VARCHAR(128)  NOT NULL,
  as_of       DATE          NOT NULL,
  PRIMARY KEY (share_code, tier_no)
) COMMENT='赎回费率分档';

CREATE TABLE nav_daily (
  share_code    VARCHAR(6)     NOT NULL,
  nav_date      DATE           NOT NULL COMMENT '净值日期（交易日）',
  unit_nav      DECIMAL(12,4)  NOT NULL COMMENT '单位净值',
  accum_nav     DECIMAL(12,4)  NULL     COMMENT '累计净值（含历次分红）',
  daily_return  DECIMAL(12,6)  NULL     COMMENT '日增长率（小数，来源方计算，已考虑分红）',
  source        VARCHAR(128)   NOT NULL,
  as_of         DATE           NOT NULL,
  PRIMARY KEY (share_code, nav_date)
) COMMENT='每日净值';

CREATE TABLE dividends (
  share_code     VARCHAR(6)    NOT NULL,
  record_date    DATE          NULL     COMMENT '权益登记日',
  ex_date        DATE          NOT NULL COMMENT '除息日',
  cash_per_unit  DECIMAL(12,6) NOT NULL COMMENT '每份派现金额（元）',
  pay_date       DATE          NULL     COMMENT '分红发放日',
  source         VARCHAR(128)  NOT NULL,
  as_of          DATE          NOT NULL,
  PRIMARY KEY (share_code, ex_date)
) COMMENT='分红记录';

CREATE TABLE managers (
  manager_name     VARCHAR(32)   NOT NULL COMMENT '基金经理姓名',
  company          VARCHAR(64)   NOT NULL COMMENT '所属基金公司',
  career_days      INT           NULL     COMMENT '累计从业天数（来源方口径）',
  total_aum        DECIMAL(20,2) NULL     COMMENT '现任基金资产总规模（元，来源方口径）',
  source           VARCHAR(128)  NOT NULL,
  as_of            DATE          NOT NULL,
  PRIMARY KEY (manager_name, company)
) COMMENT='基金经理（仅本基金池涉及的现任经理）';

CREATE TABLE fund_manager_tenures (
  fund_code     VARCHAR(6)   NOT NULL,
  manager_name  VARCHAR(32)  NOT NULL,
  start_date    DATE         NULL     COMMENT '任本基金基金经理的起始日',
  end_date      DATE         NULL     COMMENT '离任日；NULL 表示截至 as_of 仍在任',
  source        VARCHAR(128) NOT NULL COMMENT '来源，如 定期报告 doc_id',
  as_of         DATE         NOT NULL,
  PRIMARY KEY (fund_code, manager_name)
) COMMENT='基金经理任职记录（来自定期报告的基金经理简介表）';

CREATE TABLE holdings_top10 (
  fund_code      VARCHAR(6)     NOT NULL,
  report_period  VARCHAR(8)     NOT NULL COMMENT '报告期，如 2026Q2',
  rank_no        INT            NOT NULL COMMENT '按占净值比排名，1–10',
  stock_code     VARCHAR(10)    NOT NULL,
  stock_name     VARCHAR(32)    NOT NULL,
  weight         DECIMAL(8,6)   NOT NULL COMMENT '占基金资产净值比例（小数）',
  shares         DECIMAL(20,2)  NULL     COMMENT '持股数（股）',
  market_value   DECIMAL(20,2)  NULL     COMMENT '持仓市值（元）',
  source         VARCHAR(128)   NOT NULL,
  as_of          DATE           NOT NULL,
  PRIMARY KEY (fund_code, report_period, rank_no),
  KEY idx_hold_stock (stock_code)
) COMMENT='前十大股票持仓';

CREATE TABLE fund_scale (
  fund_code    VARCHAR(6)     NOT NULL,
  report_date  DATE           NOT NULL COMMENT '规模截止日（报告期末）',
  net_assets   DECIMAL(20,2)  NOT NULL COMMENT '基金资产净值（元，全部份额合计）',
  source       VARCHAR(128)   NOT NULL,
  as_of        DATE           NOT NULL,
  PRIMARY KEY (fund_code, report_date)
) COMMENT='基金规模';

CREATE TABLE period_returns (
  share_code  VARCHAR(6)     NOT NULL,
  period      VARCHAR(16)    NOT NULL COMMENT '1w 1m 3m 6m 1y 2y 3y ytd since_inception',
  ret         DECIMAL(12,6)  NULL     COMMENT '区间收益率（小数，来源方计算）；来源方无数据时为 NULL',
  end_date    DATE           NOT NULL COMMENT '区间截止日',
  source      VARCHAR(128)   NOT NULL,
  as_of       DATE           NOT NULL,
  PRIMARY KEY (share_code, period)
) COMMENT='来源方给出的区间收益';
