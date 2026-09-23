-- ---------------------------------------------------------------------------
-- Optional metadata store for service-reachable-monitor.
-- Sanitized re-creation of the tables used by the original monitor scripts
-- (environment registry, interface inventory, application ownership).
-- The tool works fine without them: owners can live in targets.yaml instead.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `monitor_environment` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'primary key',
  `zk_address`       VARCHAR(255)    NOT NULL DEFAULT '' COMMENT 'zookeeper hosts, host:port[,host:port]',
  `zk_root_path`     VARCHAR(255)    NOT NULL DEFAULT '' COMMENT 'zookeeper root path of the environment',
  `environment_name` VARCHAR(64)     NOT NULL DEFAULT '' COMMENT 'environment name, e.g. env-a',
  `ip`               VARCHAR(64)     NOT NULL DEFAULT '' COMMENT 'environment entry ip (informational)',
  `update_time`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `create_date`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_environment` (`environment_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='monitored environments';

CREATE TABLE IF NOT EXISTS `monitor_interface` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'primary key',
  `interface`   VARCHAR(512)    NOT NULL DEFAULT '' COMMENT 'dubbo interface FQCN',
  `application` VARCHAR(128)    NOT NULL DEFAULT '' COMMENT 'owning application name',
  `port`        INT             NOT NULL DEFAULT 0  COMMENT 'provider port',
  `ips`         VARCHAR(255)    NOT NULL DEFAULT '' COMMENT 'last seen provider ip',
  `update_time` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `create_date` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_interface` (`interface`(191)),
  KEY `idx_application` (`application`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='dubbo interface inventory';

CREATE TABLE IF NOT EXISTS `monitor_application` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'primary key',
  `application`      VARCHAR(128)    NOT NULL DEFAULT '' COMMENT 'application name',
  `application_desc` VARCHAR(128)    NOT NULL DEFAULT '' COMMENT 'human readable description',
  `user_name`        VARCHAR(64)     NOT NULL DEFAULT '' COMMENT 'owner name',
  `user_phone`       VARCHAR(32)     NOT NULL DEFAULT '' COMMENT 'owner phone (for @-mentions)',
  `user_mail`        VARCHAR(128)    NOT NULL DEFAULT '' COMMENT 'owner email',
  `send_flag`        TINYINT         NOT NULL DEFAULT 1  COMMENT '1 = alert, 0 = muted',
  `update_time`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `create_date`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_application` (`application`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='application ownership / contacts';