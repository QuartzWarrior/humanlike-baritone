package hb.common;

import hb.proto.v1.SchemaVersion;

/** Constants shared by hb-recorder, hb-executor and, through hb.proto, the Python pipeline. */
public final class HbCommon {
	public static final String MOD_ID = "hb-common";

	/** Stamped into every record; defined in hb.proto so Java and Python read the same number. */
	public static final int SCHEMA_VERSION = SchemaVersion.SCHEMA_VERSION_CURRENT.getNumber();

	private HbCommon() {
	}
}
