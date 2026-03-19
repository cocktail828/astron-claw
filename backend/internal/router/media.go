package router

import (
	"github.com/gin-gonic/gin"
	"github.com/rs/zerolog/log"

	"github.com/hygao1024/astron-claw/backend/internal/model"
	"github.com/hygao1024/astron-claw/backend/internal/service"
)

func (app *App) uploadMedia(c *gin.Context) {
	token, _ := c.Get("token")
	tokenStr := token.(string)

	file, header, err := c.Request.FormFile("file")
	if err != nil {
		model.ErrorResponse(c, model.ErrMediaInvalidFile)
		return
	}
	defer file.Close()

	sessionID := c.PostForm("sessionId")

	fileSize := header.Size
	if fileSize > service.MaxFileSize {
		log.Warn().Int64("size", fileSize).Msg("Media upload rejected: file too large")
		model.ErrorResponse(c, model.ErrMediaFileTooLarge)
		return
	}

	mimeType := header.Header.Get("Content-Type")
	if mimeType == "" {
		mimeType = "application/octet-stream"
	}
	fileName := header.Filename
	if fileName == "" {
		fileName = "unnamed"
	}

	result, err := app.MediaMgr.Store(file, fileName, fileSize, mimeType, sessionID)
	if err != nil {
		log.Error().Err(err).Msg("Media upload failed")
		c.JSON(500, gin.H{"code": 500, "error": err.Error()})
		return
	}
	if result == nil {
		log.Warn().Str("name", fileName).Str("mime", mimeType).Msg("Media upload rejected: invalid file")
		model.ErrorResponse(c, model.ErrMediaInvalidFile)
		return
	}

	log.Info().Str("name", fileName).Int64("size", fileSize).Str("token", tokenStr[:10]+"...").
		Msg("Media uploaded")
	c.JSON(200, gin.H{
		"code":        0,
		"fileName":    result.FileName,
		"mimeType":    result.MimeType,
		"fileSize":    result.FileSize,
		"sessionId":   result.SessionID,
		"downloadUrl": result.DownloadURL,
	})
}
