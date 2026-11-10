-- Database schema for the Agentic Order & Quote Approval System.
--
-- GENERATED FILE - do not edit. The SQLAlchemy models in app/models/ are the
-- source of truth; this is the PostgreSQL DDL they produce, provided so the
-- schema can be read without running the application.
--
-- Regenerate:  python scripts/dump_schema.py
-- Apply:       python -m app.database.seed   (create_all + idempotent seed data)

CREATE TABLE approval_policies (
	id SERIAL NOT NULL, 
	entity_type VARCHAR(20) NOT NULL, 
	capability VARCHAR(20) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	CONSTRAINT pk_approval_policies PRIMARY KEY (id)
);
CREATE INDEX ix_approval_policies_lookup ON approval_policies (entity_type, capability, is_active);

CREATE TABLE capabilities (
	id SERIAL NOT NULL, 
	name VARCHAR(20) NOT NULL, 
	CONSTRAINT pk_capabilities PRIMARY KEY (id), 
	CONSTRAINT uq_capabilities_name UNIQUE (name)
);

CREATE TABLE roles (
	id SERIAL NOT NULL, 
	name VARCHAR(50) NOT NULL, 
	CONSTRAINT pk_roles PRIMARY KEY (id), 
	CONSTRAINT uq_roles_name UNIQUE (name)
);

CREATE TABLE users (
	id SERIAL NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	email VARCHAR(255) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE TABLE approval_rules (
	id SERIAL NOT NULL, 
	policy_id INTEGER NOT NULL, 
	min_value NUMERIC(12, 2) NOT NULL, 
	max_value NUMERIC(12, 2), 
	allowed_role_id INTEGER NOT NULL, 
	priority INTEGER NOT NULL, 
	CONSTRAINT pk_approval_rules PRIMARY KEY (id), 
	CONSTRAINT ck_approval_rules_min_value_non_negative CHECK (min_value >= 0), 
	CONSTRAINT ck_approval_rules_max_gte_min CHECK (max_value IS NULL OR max_value >= min_value), 
	CONSTRAINT ck_approval_rules_priority_non_negative CHECK (priority >= 0), 
	CONSTRAINT fk_approval_rules_policy_id_approval_policies FOREIGN KEY(policy_id) REFERENCES approval_policies (id) ON DELETE CASCADE, 
	CONSTRAINT fk_approval_rules_allowed_role_id_roles FOREIGN KEY(allowed_role_id) REFERENCES roles (id)
);
CREATE INDEX ix_approval_rules_policy_id ON approval_rules (policy_id);

CREATE TABLE orders (
	id SERIAL NOT NULL, 
	created_by INTEGER NOT NULL, 
	order_value NUMERIC(12, 2) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	current_approver INTEGER, 
	CONSTRAINT pk_orders PRIMARY KEY (id), 
	CONSTRAINT ck_orders_value_positive CHECK (order_value > 0), 
	CONSTRAINT fk_orders_created_by_users FOREIGN KEY(created_by) REFERENCES users (id), 
	CONSTRAINT fk_orders_current_approver_users FOREIGN KEY(current_approver) REFERENCES users (id)
);
CREATE INDEX ix_orders_created_by ON orders (created_by);
CREATE INDEX ix_orders_status ON orders (status);

CREATE TABLE quotes (
	id SERIAL NOT NULL, 
	created_by INTEGER NOT NULL, 
	quote_value NUMERIC(12, 2) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	current_approver INTEGER, 
	CONSTRAINT pk_quotes PRIMARY KEY (id), 
	CONSTRAINT ck_quotes_value_positive CHECK (quote_value > 0), 
	CONSTRAINT fk_quotes_created_by_users FOREIGN KEY(created_by) REFERENCES users (id), 
	CONSTRAINT fk_quotes_current_approver_users FOREIGN KEY(current_approver) REFERENCES users (id)
);
CREATE INDEX ix_quotes_created_by ON quotes (created_by);
CREATE INDEX ix_quotes_status ON quotes (status);

CREATE TABLE user_capabilities (
	user_id INTEGER NOT NULL, 
	capability_id INTEGER NOT NULL, 
	CONSTRAINT pk_user_capabilities PRIMARY KEY (user_id, capability_id), 
	CONSTRAINT fk_user_capabilities_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_user_capabilities_capability_id_capabilities FOREIGN KEY(capability_id) REFERENCES capabilities (id) ON DELETE CASCADE
);

CREATE TABLE user_roles (
	user_id INTEGER NOT NULL, 
	role_id INTEGER NOT NULL, 
	CONSTRAINT pk_user_roles PRIMARY KEY (user_id, role_id), 
	CONSTRAINT fk_user_roles_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_user_roles_role_id_roles FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE
);

CREATE TABLE approval_logs (
	id SERIAL NOT NULL, 
	entity_type VARCHAR(20) NOT NULL, 
	entity_id INTEGER NOT NULL, 
	execution_id VARCHAR(36), 
	step_number INTEGER, 
	action VARCHAR(100) NOT NULL, 
	performed_by INTEGER, 
	agent_name VARCHAR(100), 
	policy_id INTEGER, 
	rule_applied INTEGER, 
	tool_called TEXT, 
	reasoning_trace TEXT, 
	timestamp TIMESTAMP WITH TIME ZONE NOT NULL, 
	remarks TEXT, 
	CONSTRAINT pk_approval_logs PRIMARY KEY (id), 
	CONSTRAINT fk_approval_logs_performed_by_users FOREIGN KEY(performed_by) REFERENCES users (id), 
	CONSTRAINT fk_approval_logs_policy_id_approval_policies FOREIGN KEY(policy_id) REFERENCES approval_policies (id), 
	CONSTRAINT fk_approval_logs_rule_applied_approval_rules FOREIGN KEY(rule_applied) REFERENCES approval_rules (id)
);
CREATE INDEX ix_approval_logs_entity ON approval_logs (entity_type, entity_id);
