package com.fundagent.backend.document.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.fundagent.backend.document.Document;
import java.util.List;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

public interface DocumentMapper extends BaseMapper<Document> {

    /**
     * 条件更新状态（乐观地保证状态机）：只有当前状态等于 {@code from} 才会更新，返回受影响行数。
     * 同时写入入库结果（pages / chunks / error）。
     */
    @Update("UPDATE documents SET status = #{to}, error = #{error}, pages = #{pages}, chunks = #{chunks} "
            + "WHERE id = #{id} AND status = #{from}")
    int transition(
            @Param("id") long id,
            @Param("from") String from,
            @Param("to") String to,
            @Param("error") String error,
            @Param("pages") Integer pages,
            @Param("chunks") Integer chunks);

    /** 超过 {@code seconds} 秒没有更新的 PENDING / PROCESSING 文档（按数据库时钟算，避免应用与库时区不一致）。 */
    @Select("SELECT * FROM documents WHERE status IN ('PENDING', 'PROCESSING') "
            + "AND updated_at < (NOW(3) - INTERVAL #{seconds} SECOND)")
    List<Document> findStale(@Param("seconds") int seconds);
}
